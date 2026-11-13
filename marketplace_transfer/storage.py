from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import re
import threading
from .domain import Product
from .history import HistoryStore
from .history_schema import initialize


def now():
    return datetime.now(timezone.utc).isoformat()


class Store(HistoryStore):
    def __init__(self, path, recover=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._history_local=threading.local()
        self.last_migration_backup=initialize(self.path)
        with self.connect() as db:
            # An interrupted write is ambiguous, never automatically retry it.
            if recover:
                db.execute("UPDATE products SET status='unknown', error='Операция прервана: требуется сверка с Авито' WHERE status='creating'")
                db.execute("UPDATE session_items SET transfer_result='unknown',error='Операция прервана: требуется сверка с Авито' WHERE transfer_result='creating' AND session_id IN (SELECT id FROM sessions WHERE status='running')")
                db.execute("UPDATE sessions SET status='interrupted',finished_at=?,detail='Сеанс прерван; выполненная работа сохранена',updated_at=? WHERE status='running'",(now(),now()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.set_settings({key:value})

    def set_settings(self, values):
        with self.connect() as db:
            db.executemany("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(key,json.dumps(value,ensure_ascii=False)) for key,value in values.items()])

    def import_products(self, products, source):
        with self.connect() as db:
            for p in products:
                existing=db.execute('SELECT data FROM products WHERE fingerprint=?',(p.key,)).fetchone()
                if existing and not p.avito_title:
                    p.avito_title=json.loads(existing[0]).get('avito_title','')
                if existing:
                    previous=json.loads(existing[0])
                    if not p.condition:p.condition=previous.get('condition','')
                    if not p.origin_country:p.origin_country=previous.get('origin_country','')
                db.execute("INSERT INTO products(fingerprint,data,source,status,error,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET data=excluded.data,source=excluded.source,updated=excluded.updated",
                           (p.key, json.dumps(asdict(p), ensure_ascii=False), str(source), "invalid" if p.errors else "unchecked", "; ".join(p.errors), now()))
        self.set_setting("source_file", str(source))

    def rows(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM products ORDER BY id")]

    def product(self, row):
        return Product(**json.loads(row["data"]))

    def active_rows(self):
        source=self.setting('source_file')
        return [row for row in self.rows() if not source or row['source']==source]

    def transition(self, pid, status, detail="", ad_id=None, url=None, photo_count=0):
        detail=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[параметры скрыты]',str(detail))
        detail=re.sub(r'(?i)(authorization|cookie|password|token)\s*[:=]\s*\S+',r'\1=[скрыто]',detail)
        with self.connect() as db:
            db.execute("UPDATE products SET status=?,error=?,ad_id=COALESCE(?,ad_id),url=COALESCE(?,url),photo_count=?,updated=? WHERE id=?",
                       (status, detail, ad_id, url, photo_count, now(), pid))
            result=db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
            if result:self._record_event(db,dict(result),status,detail)

    def update_product(self, pid, p):
        with self.connect() as db:
            db.execute("UPDATE products SET data=?,updated=? WHERE id=?", (json.dumps(asdict(p), ensure_ascii=False), now(), pid))

    def snapshot(self, data):
        compact=dict(data)
        if 'items' in data:
            compact['items']=[]
            for item in data['items']:
                copy=dict(item)
                if 'detail' in item:
                    copy['detail']={key:item['detail'][key] for key in ('path','headings','pageText') if key in item['detail']}
                compact['items'].append(copy)
        with self.connect() as db:
            db.execute("INSERT INTO snapshots(at,complete,data) VALUES(?,?,?)", (now(), bool(data.get("complete")), json.dumps(compact, ensure_ascii=False)))
            db.execute('DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 10)')

    def latest_snapshot(self):
        with self.connect() as db:
            row=db.execute('SELECT data FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def backup(self, path):
        with self.connect() as src, sqlite3.connect(path) as dst:
            src.backup(dst)
