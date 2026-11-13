from marketplace_transfer.domain import Product,compare


def test_long_marketing_title_does_not_hide_existing_same_model():
    source=Product('Выдвижные направляющие Miele FlexiClip HFC 71 с полным выдвижением, технология PerfectClean')
    existing=Product('Направляющие Miele FlexiClip HFC 71')
    assert compare(source,existing)=='review'
