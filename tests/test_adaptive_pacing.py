import httpx
import pytest
from marketplace_transfer.pacing import AdaptivePacer, failure_kind
from marketplace_transfer.storage import Store
from marketplace_transfer.domain import Product
from marketplace_transfer.engine import TransferQueue


@pytest.fixture
def pace(tmp_path):
    store=Store(tmp_path/'adaptive.db')
    store.set_settings({'adaptive_pacing_enabled':True,'target_cycle_seconds':120,
                        'adaptive_min_seconds':60,'adaptive_max_seconds':300})
    return AdaptivePacer(store)


def test_three_verified_saves_accelerate_and_backoff_recovers(pace):
    assert [pace.observe('success').cycle for _ in range(3)]==[120,120,110]
    assert pace.observe('temporary').cycle==138
    assert pace.observe('success').cycle==138
    assert pace.observe('success').cycle==138
    assert pace.observe('success').cycle==128


def test_bounds_hold_under_long_success_and_error_runs(pace):
    for _ in range(100):pace.observe('success')
    assert pace.cycle==60
    for _ in range(100):pace.observe('temporary')
    assert pace.cycle==300


def test_requested_17_to_60_range_and_small_backoff(pace):
    pace.store.set_settings({'target_cycle_seconds':60,'adaptive_min_seconds':17,'adaptive_max_seconds':60})
    assert pace.cycle==60
    for _ in range(15):pace.observe('success')
    assert pace.cycle==17
    assert pace.observe('temporary').cycle==22
    for _ in range(40):pace.observe('temporary')
    assert pace.cycle==60
    queue=TransferQueue(pace.store,None,interval=3)
    for _ in range(30):pace.observe('success')
    assert queue.next_item_delay(0,10)==7
    assert queue.next_item_delay(0,44)==3


def test_default_adaptive_range_is_17_to_60(tmp_path):
    store=Store(tmp_path/'defaults.db')
    store.set_setting('adaptive_pacing_enabled',True)
    pace=AdaptivePacer(store)
    assert pace.cycle==60
    for _ in range(40):pace.observe('success')
    assert pace.cycle==17


def test_data_error_breaks_success_streak_without_slowing(pace):
    pace.observe('success')
    pace.observe('success')
    assert pace.observe('data').cycle==120
    assert pace.observe('success').cycle==120
    assert pace.observe('skipped').cycle==120


def test_learned_cycle_survives_restart_and_new_limits(pace):
    pace.observe('temporary')
    reopened=AdaptivePacer(Store(pace.store.path))
    assert reopened.cycle==150
    reopened.store.set_setting('adaptive_max_seconds',130)
    assert reopened.cycle==120
    reopened.store.set_setting('adaptive_pacing_enabled',False)
    assert reopened.cycle==120


@pytest.mark.parametrize('error,expected',[
    (TimeoutError(''),'temporary'),(httpx.ReadTimeout(''),'temporary'),
    (httpx.ConnectError('network unavailable'),'temporary'),
    (RuntimeError('Страница не перешла в ожидаемое состояние за отведённое время'),'temporary'),
    (ValueError('Нет фотографий'),'data'),
    (RuntimeError('Проверенная категория отсутствует в подсказках текущего товара'),'data'),
    (RuntimeError('Авито требует ручную проверку безопасности'),'blocked'),
    (RuntimeError('HTTP 429 Too Many Requests'),'blocked'),
    (RuntimeError('Расширение не подключено'),'blocked'),
])
def test_failure_classification(error,expected):
    assert failure_kind(error)==expected


@pytest.mark.parametrize('status,expected',[(404,'data'),(503,'temporary'),(403,'blocked'),(429,'blocked')])
def test_http_response_classification(status,expected):
    response=httpx.Response(status,request=httpx.Request('GET','https://example.test/photo'))
    assert failure_kind(httpx.HTTPStatusError('HTTP failure',request=response.request,response=response))==expected


def test_three_temporary_errors_stop_without_retrying_unknown_writes(pace):
    store=pace.store
    store.import_products([Product(f'Item {i}',article=str(i),price='1',photos=['a']) for i in range(4)],'source')
    class Adapter:
        verified_for_batch=True
        calls=0
        def inventory(self):return {'complete':True,'products':[]}
        def create_draft(self,*args):
            self.calls+=1
            raise TimeoutError('Timed out while saving')
    adapter=Adapter()
    queue=TransferQueue(store,adapter,interval=0)
    queue.next_item_delay=lambda *args:0
    queue.check_all()
    queue.run()
    assert adapter.calls==3 and queue.stop_event.is_set()
    assert [r['status'] for r in store.rows()]==['unknown','unknown','unknown','missing']
    assert store.setting('adaptive_pacing_state')['temporary_errors']==3


def test_gui_applies_and_persists_adaptive_bounds(qtbot,tmp_path):
    from marketplace_transfer.app import Window
    window=Window(tmp_path)
    qtbot.addWidget(window)
    window.adaptive_pacing.setChecked(True)
    window.target_cycle.setValue(120)
    window.adaptive_min.setValue(60)
    window.adaptive_max.setValue(300)
    window.apply_pacing()
    assert window.store.setting('adaptive_pacing_enabled') is True
    assert AdaptivePacer(window.store).cycle==120
    messages=[]
    window.error=messages.append
    window.adaptive_min.setValue(400)
    window.apply_pacing()
    assert messages and window.store.setting('adaptive_min_seconds')==60
    window.close()
