from marketplace_transfer.storage import Store
from marketplace_transfer.domain import Product


def test_history_preserves_photo_count_per_operation(tmp_path):
    store=Store(tmp_path/'history.db')
    store.import_products([Product('Товар',price='10')],'a.xlsx')
    store.transition(1,'created',ad_id='1',url='https://www.avito.ru/draft/1',photo_count=9)
    store.transition(1,'created','Замена фото',photo_count=6)
    assert [r['photo_count'] for r in store.history()]==[6,9]


def test_other_import_not_silently_queued_and_title_override_persists(tmp_path):
    store=Store(tmp_path/'imports.db')
    first=Product('Первый товар',price='10')
    store.import_products([first],'a.xlsx')
    first.avito_title='Короткое название'
    store.update_product(1,first)
    store.import_products([Product('Первый товар',price='10')],'a.xlsx')
    assert store.product(store.rows()[0]).avito_title=='Короткое название'
    store.import_products([Product('Второй товар',price='20')],'b.xlsx')
    assert len(store.rows())==2
    assert len(store.active_rows())==1
    assert store.product(store.active_rows()[0]).title=='Второй товар'
