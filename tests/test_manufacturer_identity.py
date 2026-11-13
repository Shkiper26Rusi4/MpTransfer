from marketplace_transfer.domain import Product,compare


def test_distinct_explicit_manufacturer_articles_are_distinct_products():
    a=Product('Ароматизатор Miele Aqua, 12,5 мл, 1 шт',article='1111',description='Артикул Miele: 1111')
    b=Product('Ароматизатор Miele Cocoon, 12,5 мл, 1 шт',article='2222',extra={'article_namespace':'manufacturer:Miele'})
    assert compare(a,b)=='different'


def test_seller_articles_alone_do_not_disprove_duplicate():
    assert compare(Product('Фильтр Miele',article='seller-a'),Product('Фильтр Miele',article='seller-b'))=='review'
