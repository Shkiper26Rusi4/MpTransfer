from dataclasses import asdict
import json
import sqlite3
import pytest
from marketplace_transfer.domain import Product
from marketplace_transfer.storage import Store
from marketplace_transfer.history import export_history
from marketplace_transfer.history_schema import SCHEMA_VERSION


def legacy_database(path):
    product=Product('Legacy product',article='A1',price='100')
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE products(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE,data TEXT,source TEXT,status TEXT,ad_id TEXT,url TEXT,error TEXT,updated TEXT,photo_count INTEGER);
        CREATE TABLE events(id INTEGER PRIMARY KEY,product_id INTEGER,at TEXT,status TEXT,detail TEXT,ad_id TEXT,url TEXT,photo_count INTEGER);
        CREATE TABLE snapshots(id INTEGER PRIMARY KEY,at TEXT,complete INTEGER,data TEXT);PRAGMA user_version=2;''')
        db.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?)',(1,product.key,json.dumps(asdict(product)),'old.xlsx','created','555','https://www.avito.ru/draft/555','','2026-11-01T10:00:00+00:00',8))
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',(1,1,'2026-11-01T10:00:00+00:00','created','Verified','555','https://www.avito.ru/draft/555',8))
    return path


def test_old_schema_migration_preserves_every_link_and_event(tmp_path):
    path=legacy_database(tmp_path/'old.db')
    store=Store(path)
    assert store.last_migration_backup.is_file()
    assert store.history()[0]['ad_id']=='555'
    assert store.rows()[0]['url']=='https://www.avito.ru/draft/555'
    assert store.sessions()[0]['kind']=='legacy'
    assert store.sessions()[0]['duration_seconds'] is None
    again=Store(path)
    assert len(again.history())==1 and len(again.sessions())==1
    assert again.last_migration_backup is None
    with sqlite3.connect(path) as db:assert db.execute('pragma user_version').fetchone()[0]==SCHEMA_VERSION


def test_sessions_restore_frozen_product_data_and_duration(tmp_path):
    store=Store(tmp_path/'history.db')
    product=Product('Original',article='A1',price='100')
    store.import_products([product],'first.xlsx')
    with store.session('transfer',[1]) as sid:
        store.transition(1,'creating')
        store.transition(1,'created',ad_id='10',url='https://www.avito.ru/draft/10',photo_count=9)
    product.title='Renamed after transfer'
    store.update_product(1,product)
    reopened=Store(store.path)
    assert json.loads(reopened.history(sid)[0]['data'])['title']=='Original'
    session=reopened.sessions()[0]
    assert session['source_file']=='first.xlsx' and session['created']==1
    assert session['duration_seconds']>=0 and session['status']=='completed'
    assert reopened.session_items(sid)[0]['photo_count']==9
    export_history(reopened,tmp_path/'history.xlsx',sid)
    from openpyxl import load_workbook
    wb=load_workbook(tmp_path/'history.xlsx',read_only=True)
    assert wb['Все операции'].max_row==3
    assert wb['Товары сеансов'].cell(2,3).value=='Original'
    wb.close()


def test_interrupted_session_is_retained_without_new_drafts(tmp_path):
    store=Store(tmp_path/'crash.db')
    store.import_products([Product('A',price='1')],'input')
    with store.session('transfer',[1]) as sid:
        store.transition(1,'creating')
        with store.connect() as db:
            db.execute("UPDATE sessions SET status='interrupted' WHERE id=?",(sid,))
    # Simulate power loss before the session context finished.
    with store.connect() as db:db.execute("UPDATE sessions SET status='running',finished_at=NULL WHERE id=?",(sid,))
    reopened=Store(store.path)
    assert reopened.rows()[0]['status']=='unknown'
    assert reopened.sessions()[0]['status']=='interrupted'
    assert reopened.session_items(sid)[0]['transfer_result']=='unknown'


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path,monkeypatch):
    path=legacy_database(tmp_path/'old.db')
    def fail():raise RuntimeError('Migration failure')
    monkeypatch.setattr('marketplace_transfer.history_schema.stamp',fail)
    with pytest.raises(RuntimeError):Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('pragma user_version').fetchone()[0]==2
        assert db.execute('select ad_id from products').fetchone()[0]=='555'
        assert 'session_id' not in {r[1] for r in db.execute('pragma table_info(events)')}
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1


def test_history_filters_and_restart_show_real_rows(qtbot,tmp_path):
    from marketplace_transfer.app import Window
    store=Store(legacy_database(tmp_path/'transfer.sqlite3'))
    window=Window(tmp_path)
    qtbot.addWidget(window)
    assert window.history_table.rowCount()==1
    window.history_search.setText('no match')
    assert window.history_table.rowCount()==0
    assert 'Записей в базе: 1' in window.history_summary.text()
    window.reset_history_filters()
    assert window.history_table.rowCount()==1
    window.close()
    again=Window(tmp_path)
    qtbot.addWidget(again)
    assert again.history_table.rowCount()==1
    assert 'Черновиков в базе: 1' in again.metrics.text()
    again.close()


def test_check_session_snapshots_previously_created_links(tmp_path):
    from marketplace_transfer.engine import TransferQueue
    store=Store(tmp_path/'checked.db')
    store.import_products([Product('Existing',price='100')],'input.xlsx')
    store.transition(1,'created',ad_id='555',url='https://www.avito.ru/draft/555',photo_count=8)
    class Adapter:
        def inventory(self):return {'complete':True,'products':[],'items':[]}
    TransferQueue(store,Adapter()).check_all()
    session=next(s for s in store.sessions() if s['kind']=='check')
    item=store.session_items(session['id'])[0]
    assert item['check_result']=='created' and item['ad_id']=='555'
    assert item['url']=='https://www.avito.ru/draft/555' and item['photo_count']==8
    assert session['created']==0  # Old drafts are not counted as new transfers.


def test_transfer_history_includes_duplicate_check_result(tmp_path):
    from marketplace_transfer.engine import TransferQueue
    store=Store(tmp_path/'transfer.db')
    store.import_products([Product('Product',price='100',photos=['photo.jpg'])],'input.xlsx')
    store.transition(1,'missing')
    class Adapter:
        verified_for_batch=True
        def inventory(self):return {'complete':True,'products':[],'items':[]}
        def create_draft(self,*args):return {'verified':True,'status':'draft','id':'777','url':'https://www.avito.ru/draft/777','photo_count':1}
    TransferQueue(store,Adapter(),interval=0).run()
    session=next(s for s in store.sessions() if s['kind']=='transfer')
    item=store.session_items(session['id'])[0]
    assert item['check_result']=='missing' and item['transfer_result']=='created'
    assert item['ad_id']=='777' and session['created']==1


def test_check_session_counts_newly_found_existing_goods(qtbot,tmp_path):
    from marketplace_transfer.engine import TransferQueue
    from marketplace_transfer.app import Window
    store=Store(tmp_path/'transfer.sqlite3')
    product=Product('Filter',article='A1',price='100')
    store.import_products([product],'input.xlsx')
    class Adapter:
        def inventory(self):return {'complete':True,'products':[asdict(product)],'items':[]}
    TransferQueue(store,Adapter()).check_all()
    session=next(s for s in store.sessions() if s['kind']=='check')
    assert session['existing_before']==0 and session['existing']==1
    window=Window(tmp_path);qtbot.addWidget(window)
    window.history_session.setCurrentIndex(window.history_session.findData(session['id']))
    assert 'Найдены существующие: 1' in window.history_summary.text()
    window.close()
