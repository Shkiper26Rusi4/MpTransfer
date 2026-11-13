from pathlib import Path
import csv
import io
import re
from .domain import Product, norm

LABELS = {
    "title": "Название", "article": "Артикул", "sku": "SKU", "ozon_id": "Ozon Product ID",
    "brand": "Бренд", "model": "Модель", "category": "Категория", "description": "Описание",
    "price": "Цена", "kit": "Комплектация", "quantity": "Количество в упаковке",
    "dimensions": "Размеры", "volume": "Объём", "compatibility": "Совместимость", "photos": "Фотографии",
    "condition": "Состояние", "origin_country": "Страна происхождения",
}
ALIASES = {
    "title": ["название для avito", "название товара", "наименование", "наименование товара", "название", "product name", "title"],
    "article": ["артикул", "артикул продавца", "offer id", "offer_id"],
    "sku": ["sku", "ozon sku", "sku ozon"], "ozon_id": ["ozon id", "ozon product id", "id товара"],
    "brand": ["бренд", "brand", "торговая марка"], "model": ["модель", "model"],
    "category": ["категория", "категория ozon", "тип товара", "category"], "description": ["описание для avito", "описание", "аннотация", "description"],
    "price": ["цена для avito ₽", "цена", "цена руб", "цена с учетом скидки", "ваша цена", "price"],
    "kit": ["комплектация", "комплект"], "quantity": ["количество в упаковке", "количество предметов в упаковке", "штук в упаковке"],
    "dimensions": ["размеры", "габариты"], "volume": ["объем", "объём"],
    "compatibility": ["совместимость"], "photos": ["фотографии", "ссылки на фото", "ссылка на главное фото", "главное фото", "photos"],
    "condition": ["состояние", "состояние товара"], "origin_country": ["страна происхождения", "страна происхождения товара", "страна производства"],
}


def guess(headers):
    result = {}
    for field, aliases in ALIASES.items():
        for i, header in enumerate(headers):
            if norm(header) in [norm(a) for a in aliases]:
                result[field] = i
                break
    return result


def read_table(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            rows = list(wb.active.values)
        finally:
            wb.close()
    elif ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path), on_demand=True)
        try:
            sheet = wb.sheet_by_index(0)
            rows = [sheet.row_values(i) for i in range(sheet.nrows)]
        finally:
            wb.release_resources()
    elif ext == ".csv":
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        try:
            dialect = csv.Sniffer().sniff(text[:16384], delimiters=",;\t")
        except csv.Error:
            # Sniffer can fail on quoted multiline descriptions or Ozon preambles.
            # Parse candidates as CSV, preserving quoted newlines, and use header evidence.
            candidates = [list(csv.reader(io.StringIO(text), delimiter=d)) for d in (",", ";", "\t")]
            rows = max(candidates, key=lambda table: max((len(guess(row)), len(row)) for row in table[:30]) if table else (0,0))
        else:
            rows = list(csv.reader(io.StringIO(text), dialect))
    else:
        raise ValueError("Поддерживаются XLSX, XLS и CSV")
    if not rows:
        raise ValueError("Таблица пуста")
    # Ozon exports may contain introductory rows. Only consider a bounded header area.
    header_index = max(range(min(30, len(rows))), key=lambda i: len(guess(rows[i])))
    headers = [str(x or "").strip() or f"Столбец {i+1}" for i, x in enumerate(rows[header_index])]
    return headers, [list(row) for row in rows[header_index+1:] if any(x is not None and str(x).strip() for x in row)]


def products_from_rows(headers, rows, mapping):
    result = []
    if "title" not in mapping:
        raise ValueError("Выберите столбец названия")
    for row in rows:
        def cell_text(value):
            if isinstance(value,float) and value.is_integer():return str(int(value))
            return str(value).strip() if value is not None else ''
        values = {field: cell_text(row[index]) if index < len(row) else "" for field, index in mapping.items()}
        photos = values.pop("photos", "")
        values["photos"] = [x.strip() for x in re.split(r"[;\n|]+", photos) if x.strip()]
        for i, header in enumerate(headers):
            if i not in mapping.values() and norm(header) in {"дополнительные фото", "дополнительные фотографии", "ссылки на дополнительные фото"} and i < len(row) and row[i]:
                values["photos"].extend(x.strip() for x in re.split(r"[;\n|]+", str(row[i])) if x.strip())
        values["photos"] = list(dict.fromkeys(values["photos"]))
        used = set(mapping.values())
        values["extra"] = {f"{h} [{i+1}]": str(row[i]) for i, h in enumerate(headers) if i not in used and i < len(row) and row[i] is not None}
        p = Product(**values)
        p.validate()
        result.append(p)
    return result
