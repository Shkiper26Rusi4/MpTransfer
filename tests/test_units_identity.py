from marketplace_transfer.domain import Product,compare


def test_volume_attached_to_unit_is_not_a_different_variant():
    assert compare(Product('Контейнер Miele MB-CM-G, 0,7 л'),Product('Контейнер Miele MB-CM-G 0,7л'))=='review'
    assert compare(Product('Контейнер Miele MB-CM-G, 0.70 л'),Product('Контейнер Miele MB-CM-G 0,7л'))=='review'


def test_real_different_volumes_are_distinct():
    assert compare(Product('Контейнер Miele 0,7л'),Product('Контейнер Miele 1л'))=='different'
