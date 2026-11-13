from marketplace_transfer.importing import products_from_rows, guess


def test_real_export_headers_and_all_photos():
    headers=['Артикул','Название для Avito','Цена для Avito, ₽','Цена Ozon на сайте, ₽','Описание для Avito','Главное фото','Дополнительные фото']
    row=['11639210','Фильтр','13700','7904','Описание','https://cdn.example/1.jpg','https://cdn.example/2.jpg\nhttps://cdn.example/3.jpg\nhttps://cdn.example/1.jpg']
    p=products_from_rows(headers,[row],guess(headers))[0]
    assert p.title=='Фильтр' and p.price=='13700' and p.description=='Описание'
    assert p.photos==['https://cdn.example/1.jpg','https://cdn.example/2.jpg','https://cdn.example/3.jpg']
