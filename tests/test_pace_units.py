import math
import pytest
from marketplace_transfer.pacing import seconds_to_hourly, hourly_to_seconds
from marketplace_transfer.storage import Store
from marketplace_transfer.domain import Product
from marketplace_transfer.engine import TransferQueue, classify_snapshot


@pytest.mark.parametrize('seconds,hourly',[(0,0),(45,80),(60,60),(72,50),(100,36),(3600,1)])
def test_pace_unit_conversion(seconds,hourly):
    assert seconds_to_hourly(seconds)==pytest.approx(hourly)
    assert hourly_to_seconds(hourly)==pytest.approx(seconds)


@pytest.mark.parametrize('value',[-1,float('inf'),float('nan'),'invalid',None])
def test_invalid_pace_units(value):
    with pytest.raises(ValueError):seconds_to_hourly(value)


def test_linked_gui_fields_preserve_results_and_restart(qtbot,tmp_path):
    from marketplace_transfer.app import Window
    store=Store(tmp_path/'transfer.sqlite3')
    store.import_products([Product('Already saved',article='1',price='1')],'input')
    store.transition(1,'created',ad_id='123',url='https://www.avito.ru/draft/123',photo_count=9)
    store.set_settings({'target_cycle_seconds':100,'adaptive_pacing_enabled':True,
                        'adaptive_min_seconds':45,'adaptive_max_seconds':100,
                        'adaptive_pacing_state':{'cycle':47,'configuration':[45,100,100]}})
    before=store.rows()[0]
    window=Window(tmp_path)
    qtbot.addWidget(window)
    assert window.target_cycle.value()==100 and window.target_hourly.value()==36
    assert store.setting('adaptive_pacing_state')['cycle']==47
    window.target_hourly.setValue(50)
    assert window.target_cycle.value()==72
    window.target_cycle.setValue(45)
    assert window.target_hourly.value()==80
    window.target_hourly.setValue(52.5)
    assert window.target_cycle.value()==68.57
    assert window.target_hourly.value()==52.5
    window.apply_pacing()
    assert store.setting('target_cycle_seconds')==68.57
    assert store.rows()[0]==before
    assert store.setting('adaptive_min_seconds')==45
    assert store.setting('adaptive_max_seconds')==100
    window.close()
    reopened=Window(tmp_path)
    qtbot.addWidget(reopened)
    assert reopened.target_cycle.value()==68.57
    assert reopened.target_hourly.value()==52.5
    reopened.target_cycle.setValue(0)
    assert reopened.target_hourly.value()==0
    assert 'Начальный темп выбирается автоматически' in reopened.pace_explanation.text()
    reopened.adaptive_pacing.setChecked(False)
    assert 'Плановое ограничение отключено' in reopened.pace_explanation.text()
    reopened.target_hourly.setValue(0.25)
    assert reopened.target_cycle.value()==3600 and reopened.target_hourly.value()==1
    reopened.close()


def test_user_confirmed_manual_goods_never_requeued(tmp_path):
    store=Store(tmp_path/'confirmed.db')
    product=Product('Handled manually',article='MANUAL',price='10',photos=['a'])
    store.import_products([product],'input')
    store.transition(1,'unknown','Previous timeout')
    store.set_setting('match_decision:'+product.key,{'kind':'user_completed','confirmed_by':'user','source':'manual'})
    class Adapter:
        verified_for_batch=True
        def inventory(self):return {'complete':True,'products':[],'items':[]}
        def create_draft(self,*args):raise AssertionError('User-confirmed goods must not be created again')
    queue=TransferQueue(store,Adapter(),interval=0)
    queue.check_all()
    queue.run()
    assert store.rows()[0]['status']=='exists'
    assert store.rows()[0]['ad_id'] is None
    assert store.rows()[0]['error']=='Пользователь подтвердил: внесено вручную'
    assert classify_snapshot(store,product,{'complete':False,'products':[]})=='exists'


def test_scan_does_not_emit_to_deleted_previous_gui_worker(tmp_path):
    store=Store(tmp_path/'scan.db')
    store.import_products([Product('Product',price='10')],'input')
    def stale_callback(text):raise RuntimeError('Signal source has been deleted')
    class Adapter:
        progress=staticmethod(stale_callback)
        def inventory(self):
            self.progress('Account section')
            return {'complete':True,'products':[],'items':[]}
    assert TransferQueue(store,Adapter()).check_all()['complete']
    assert store.rows()[0]['status']=='missing'
