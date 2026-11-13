import io
import json
from pathlib import Path
import struct
import threading
import time
import zipfile
import pytest
from PIL import Image
from marketplace_transfer.domain import Product, compare, classify
from marketplace_transfer.storage import Store
from marketplace_transfer.importing import guess, read_table, products_from_rows
from marketplace_transfer.photos import cache_photo, match_files, unpack_zip
from marketplace_transfer.bridge import Bridge, BridgeError
from marketplace_transfer.engine import TransferQueue, AvitoAdapter
from marketplace_transfer.native_host import read_message, write_message


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "db.sqlite3")


@pytest.mark.parametrize("count", [10, 120, 500])
def test_xlsx(tmp_path, count):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Выгрузка Ozon"])
    ws.append(["Артикул продавца", "Название товара", "Цена, руб."])
    for i in range(count):
        ws.append([f"A{i}", f"Товар {i}", 1200])
    path = tmp_path / "source.xlsx"
    wb.save(path)
    headers, rows = read_table(path)
    products = products_from_rows(headers, rows, guess(headers))
    assert len(products) == count
    assert not products[0].errors
    assert products[0].article == "A0"


def test_csv(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text('SKU;Название;Цена;Комплектация\n001;Фильтр;120,50;4 штуки\n', encoding="cp1251")
    headers, rows = read_table(path)
    p = products_from_rows(headers, rows, guess(headers))[0]
    assert p.sku == "001" and not p.errors


def test_unknown_columns():
    p = products_from_rows(["Название", "Цена", "Материал"], [["Товар", "42", "Сталь"]], {"title": 0, "price": 1})[0]
    assert p.extra == {"Материал [3]": "Сталь"}
    assert not p.brand


@pytest.mark.parametrize("price", ["", "nan", "-1", "0", "inf", "abc"])
def test_invalid_price(price):
    assert Product("a", price=price).validate()


def test_variants_not_duplicate():
    a = Product("Miele HyClean GN 4 мешка", article="GN", quantity="4")
    b = Product("Miele HyClean GN XXL 16 мешков", article="GN", quantity="16")
    assert a.key != b.key
    assert compare(a, b) == "different"


def test_exact_article_and_variant():
    assert compare(Product("Фильтр", article="A", quantity="4"), Product("Фильтр", article="A", quantity="4")) == "exists"


def test_missing_variant_requires_review():
    assert compare(Product("Фильтр", article="A", quantity="4"), Product("Фильтр", article="A")) == "review"


def test_incomplete_inventory():
    assert classify(Product("Пылесос"), [], False) == "unchecked"
    assert classify(Product("Пылесос"), [], True) == "missing"


def test_missing_model_number_only_matters_for_related_titles():
    assert compare(Product('HEPA-фильтр Miele SF-HA 60'),Product('TEST'))=='different'
    assert compare(Product('HEPA-фильтр Miele SF-HA 60'),Product('HEPA-фильтр Miele SF-HA'))=='review'


def test_idempotent_import_and_restart(store):
    p = Product("a", price="100")
    store.import_products([p, p], "source")
    assert len(store.rows()) == 1
    store.transition(1, "created", ad_id="123", url="https://www.avito.ru/123")
    store.import_products([p], "source")
    assert Store(store.path).rows()[0]["status"] == "created"


def test_recover_unknown(store):
    store.import_products([Product("a")], "source")
    store.transition(1, "creating")
    assert Store(store.path).rows()[0]["status"] == "unknown"


def test_settings_backup(store, tmp_path):
    store.set_setting("theme", 1)
    path = tmp_path / "backup.db"
    store.backup(path)
    assert Store(path).setting("theme") == 1


def test_photos_unchanged(tmp_path):
    original = tmp_path / "SKU_1.png"
    Image.new("RGB", (64, 48), "red").save(original)
    cached = cache_photo(str(original), tmp_path / "cache")
    assert Path(cached).read_bytes() == original.read_bytes()


def test_photo_matching():
    a, b = Product("a", sku="123"), Product("b", sku="1234")
    result, ambiguous = match_files([a,b], ["123_1.jpg", "1234.jpg", "12345.jpg"])
    assert result[a.key] == ["123_1.jpg"]
    assert result[b.key] == ["1234.jpg"]
    assert not ambiguous


def test_photo_ambiguity():
    a, b = Product("a", sku="A"), Product("b", sku="A")
    result, ambiguous = match_files([a,b], ["A.jpg"])
    assert ambiguous and not any(result.values())


def test_zip_traversal(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("../escape.jpg", "bad")
    with pytest.raises(ValueError):
        unpack_zip(path, tmp_path / "out")


def test_native_framing():
    stream = io.BytesIO()
    write_message(stream, {"value": "Русский текст"})
    stream.seek(0)
    assert read_message(stream) == {"value": "Русский текст"}
    with pytest.raises(EOFError):
        read_message(io.BytesIO(b"\x01"))
    with pytest.raises(ValueError):
        read_message(io.BytesIO(struct.pack("<I", 30*1024*1024)))


def test_bridge_disconnected_and_timeout(tmp_path):
    bridge = Bridge(tmp_path)
    try:
        with pytest.raises(BridgeError, match="не подключено"):
            bridge.call("diagnose")
        bridge.last_seen = time.monotonic()
        with pytest.raises(BridgeError, match="тайм-аут"):
            bridge.call("diagnose", timeout=.02)
    finally:
        bridge.close()


def test_queue_pause_stop(store):
    q = TransferQueue(store, None)
    q.pause()
    result = []
    thread = threading.Thread(target=lambda: result.append(q.checkpoint()))
    thread.start()
    assert thread.is_alive()
    q.stop()
    thread.join(1)
    assert result == [False]


class FakeAdapter:
    # Test double only; never used in the application.
    verified_for_batch = True
    def inventory(self):
        return {"products": [], "complete": True}
    def create_draft(self, p, photos):
        return {"verified": True, "id": "1", "url": "https://www.avito.ru/1", "status": "draft", "photo_count": len(photos)}


def test_queue_verified_and_no_repeat(store):
    store.import_products([Product("a", price="1", photos=["a.jpg"])], "source")
    q = TransferQueue(store, FakeAdapter(), interval=0)
    q.check_all()
    q.run()
    assert store.rows()[0]["status"] == "created"
    q.run()
    assert sum(x["status"] == "created" for x in store.history()) == 1


def test_unknown_save_not_retried(store):
    class TimeoutAdapter(FakeAdapter):
        def create_draft(self, *args):
            raise TimeoutError("test timeout")
    store.import_products([Product("a", price="1", photos=["a.jpg"])], "source")
    q = TransferQueue(store, TimeoutAdapter(), interval=0)
    q.check_all()
    q.run()
    assert store.rows()[0]["status"] == "unknown"
    q.check_all()
    assert store.rows()[0]["status"] == "review"


def test_batch_gate(store):
    with pytest.raises(BridgeError, match="заблокирован"):
        TransferQueue(store, AvitoAdapter(None)).run()


def test_gui(qtbot, tmp_path):
    from marketplace_transfer.app import Window
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    for index in range(8):
        window.navigation.setCurrentRow(index)
        assert window.pages.currentIndex() == index
    window.theme.setCurrentIndex(1)
    window.save_settings()
    assert window.store.setting("theme") == 1
    window.close()
