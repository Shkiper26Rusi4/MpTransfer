import xlwt
from marketplace_transfer.importing import read_table,products_from_rows,guess
from marketplace_transfer.domain import Product
from marketplace_transfer.drafts import fill_details


def test_actual_xls_numeric_identifiers_and_price(tmp_path):
    wb=xlwt.Workbook()
    ws=wb.add_sheet('Товары')
    for col,value in enumerate(['Артикул','SKU','Название','Цена']):ws.write(0,col,value)
    for col,value in enumerate([11639210,'000123','Фильтр',13700.0]):ws.write(1,col,value)
    path=tmp_path/'source.xls'
    wb.save(str(path))
    headers,rows=read_table(path)
    product=products_from_rows(headers,rows,guess(headers))[0]
    assert product.article=='11639210'
    assert product.sku=='000123'
    assert product.price=='13700'


def test_price_13700_point_zero_is_filled_as_whole_rubles():
    class Nav:
        def inspect(self,**payload):
            if payload.get('name')=='description':return {'filled':{'verified':True}}
            assert payload['value']=='13700'
            return {'controls':[{'marker':'price','value':'13\u00a0700'}]}
    fill_details(Nav(),Product('Фильтр',description='Описание',price='13700.0'))


def test_narrow_space_russian_price_validates():
    assert not Product('Фильтр',price='13\u202f700').validate()
