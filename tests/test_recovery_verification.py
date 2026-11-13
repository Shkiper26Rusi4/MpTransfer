from marketplace_transfer.storage import Store
from marketplace_transfer.domain import Product
from marketplace_transfer.engine import TransferQueue


def test_unknown_known_id_is_verified_instead_of_recreated(tmp_path):
    store=Store(tmp_path/'recover.db')
    store.import_products([Product('Фильтр',price='100',photos=['one.jpg'])],'source')
    store.transition(1,'unknown',ad_id='555',url='https://www.avito.ru/draft/555')
    class Adapter:
        def inventory(self):return {'complete':True,'products':[]}
        def recover_saved(self,row,p):
            assert row['ad_id']=='555'
            return {'verified':True,'id':'555','url':row['url'],'photo_count':1}
        def create_draft(self,*args):raise AssertionError('Recovery must never create another draft')
    TransferQueue(store,Adapter()).check_all()
    assert store.rows()[0]['status']=='created'
    assert store.rows()[0]['photo_count']==1
