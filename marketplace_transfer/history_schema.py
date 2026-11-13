"""Additive, transactional migrations. Existing goods, links and events are retained."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

SCHEMA_VERSION=3


def stamp():
    return datetime.now(timezone.utc).isoformat()


def initialize(path):
    path=Path(path)
    existed=path.exists() and path.stat().st_size>0
    db=sqlite3.connect(path,timeout=20)
    db.row_factory=sqlite3.Row
    backup=None
    try:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA_VERSION:
            raise RuntimeError('База создана более новой версией программы. Обновите Marketplace Transfer; данные не изменены.')
        if version==SCHEMA_VERSION:
            return None
        if existed:
            directory=path.parent/'backups'
            directory.mkdir(exist_ok=True)
            backup=directory/f'pre-schema-{SCHEMA_VERSION}-{uuid.uuid4().hex}.sqlite3'
            with sqlite3.connect(backup) as target:db.backup(target)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        for sql in [
            'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
            "CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,data TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'unchecked',ad_id TEXT,url TEXT,error TEXT NOT NULL DEFAULT '',updated TEXT NOT NULL,photo_count INTEGER NOT NULL DEFAULT 0)",
            'CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,product_id INTEGER,at TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,at TEXT NOT NULL,complete INTEGER NOT NULL,data TEXT NOT NULL)',
            "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,kind TEXT NOT NULL,source_file TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,duration_seconds REAL,source_total INTEGER NOT NULL,queued_total INTEGER NOT NULL,existing_before INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS session_items(session_id TEXT NOT NULL,product_id INTEGER NOT NULL,product_data TEXT NOT NULL,source_file TEXT NOT NULL,check_result TEXT NOT NULL DEFAULT '',transfer_result TEXT NOT NULL DEFAULT '',ad_id TEXT,url TEXT,photo_count INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,PRIMARY KEY(session_id,product_id))",
        ]:db.execute(sql)
        columns={row[1] for row in db.execute('PRAGMA table_info(events)')}
        for name,kind in [('ad_id','TEXT'),('url','TEXT'),('photo_count','INTEGER'),('session_id','TEXT'),('product_data','TEXT'),('source_file','TEXT')]:
            if name not in columns:db.execute(f'ALTER TABLE events ADD COLUMN {name} {kind}')
        db.execute('CREATE INDEX IF NOT EXISTS events_session_id ON events(session_id,id)')
        db.execute('CREATE INDEX IF NOT EXISTS sessions_started_at ON sessions(started_at)')
        db.execute("UPDATE events SET product_data=COALESCE(product_data,(SELECT data FROM products WHERE products.id=events.product_id),'{}'),source_file=COALESCE(source_file,(SELECT source FROM products WHERE products.id=events.product_id),'')")
        groups=db.execute("SELECT source_file,substr(at,1,10) day,MIN(at) first,MAX(at) last,COUNT(DISTINCT product_id) total FROM events WHERE session_id IS NULL GROUP BY source_file,substr(at,1,10)").fetchall()
        for group in groups:
            sid='legacy-'+uuid.uuid4().hex
            detail='Восстановленная история: точные границы сеансов и длительность в старой версии не записывались.'
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid,'legacy',group['source_file'],group['first'],group['last'],'historical',None,group['total'],group['total'],0,detail,stamp()))
            # Explicit columns prevent accidental schema drift in old databases.
            db.execute('UPDATE events SET session_id=? WHERE session_id IS NULL AND source_file=? AND substr(at,1,10)=?',(sid,group['source_file'],group['day']))
            latest=db.execute('SELECT e.* FROM events e WHERE e.id IN (SELECT MAX(id) FROM events WHERE session_id=? GROUP BY product_id)',(sid,)).fetchall()
            for event in latest:
                if event['product_id'] is None:continue
                checking=event['status'] if event['status'] in ('exists','missing','review','unchecked','invalid') else ''
                transferring=event['status'] if not checking else ''
                db.execute('INSERT INTO session_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,event['product_id'],event['product_data'],event['source_file'],checking,transferring,event['ad_id'],event['url'],event['photo_count'] or 0,event['detail'],event['at']))
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        db.commit()
        return backup
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
