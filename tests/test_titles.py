import pytest
from marketplace_transfer.titles import avito_title


def test_title_shortening_preserves_variant():
    result=avito_title('Ароматизатор Miele Aqua для сушильных машин, 12,5 мл, 1 шт')
    assert result=='Ароматизатор Miele Aqua, 12,5 мл, 1 шт'
    assert len(result)<=50


def test_never_blindly_truncate_model_or_quantity():
    with pytest.raises(ValueError):
        avito_title('Очень длинное обозначение модели, которое невозможно сократить 1234 XXL 16 шт')
