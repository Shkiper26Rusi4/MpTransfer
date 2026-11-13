import threading
import time
import pytest
from marketplace_transfer.storage import Store
from marketplace_transfer.engine import TransferQueue
from marketplace_transfer.domain import Product


@pytest.mark.parametrize('duration,expected',[(44,76),(90,30),(130,3)])
def test_two_minute_cycle_includes_work_time(tmp_path,duration,expected):
    store=Store(tmp_path/'pace.db')
    store.set_setting('target_cycle_seconds',120)
    assert TransferQueue(store,None,interval=3).next_item_delay(100,100+duration)==expected


def test_stop_interrupts_pacing_and_keeps_saved_card(tmp_path):
    store=Store(tmp_path/'stop.db')
    store.set_setting('target_cycle_seconds',120)
    store.import_products([Product('One',article='1',price='1',photos=['a']),
                           Product('Two',article='2',price='1',photos=['a'])],'input')
    class Adapter:
        verified_for_batch=True
        def inventory(self):return {'complete':True,'products':[]}
        def create_draft(self,p,photos):
            return {'verified':True,'status':'draft','id':p.article,'url':'https://www.avito.ru/draft/'+p.article,'photo_count':1}
    waiting=threading.Event()
    def progress(data):
        if data['stage'].startswith('Ожидание заданного темпа'):waiting.set()
    queue=TransferQueue(store,Adapter(),callback=progress,interval=0)
    queue.check_all()
    worker=threading.Thread(target=queue.run)
    worker.start()
    try:
        assert waiting.wait(2)
        queue.pause()
        queue.stop()
        worker.join(1)
        assert not worker.is_alive()
        assert [r['status'] for r in store.rows()]==['created','missing']
    finally:
        queue.stop()
        worker.join(2)


def test_pacing_setting_survives_restart_and_gui_apply(qtbot,tmp_path):
    from marketplace_transfer.app import Window
    window=Window(tmp_path)
    qtbot.addWidget(window)
    window.target_cycle.setValue(120)
    window.apply_pacing()
    window.close()
    assert Store(tmp_path/'transfer.sqlite3').setting('target_cycle_seconds')==120
