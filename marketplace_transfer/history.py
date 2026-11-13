from contextlib import contextmanager
from pathlib import Path
import json
import re
import time
import uuid
from .history_schema import stamp


def safe_text(text):
    text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[параметры скрыты]',str(text))
    return re.sub(r'(?i)(authorization|cookie|password|token)\s*[:=]\s*\S+',r'\1=[скрыто]',text)


class HistoryStore:
    @contextmanager
    def session(self,kind,product_ids=None):
        rows=self.active_rows()
        selected=rows if product_ids is None else [r for r in rows if r['id'] in set(product_ids)]
        sid=uuid.uuid4().hex
        started=stamp()
        previous=getattr(self._history_local,'context',None)
        context={'id':sid,'kind':kind,'started':time.monotonic()}
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid,kind,self.setting('source_file',''),started,None,'running',0,len(rows),len(selected),sum(r['status'] in ('exists','created') for r in rows), '',started))
            for row in selected:
                checking='created' if kind=='check' and row['status']=='created' else ''
                db.execute('INSERT INTO session_items(session_id,product_id,product_data,source_file,check_result,ad_id,url,photo_count,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(sid,row['id'],row['data'],row['source'],checking,row['ad_id'],row['url'],row['photo_count'],started))
        self._history_local.context=context
        try:
            yield sid
        except BaseException as exc:
            self.finish_session(sid,'failed',safe_text(exc))
            raise
        else:
            self.finish_session(sid,'completed')
        finally:
            self._history_local.context=previous

    def finish_session(self,sid,status='completed',detail=''):
        context=getattr(self._history_local,'context',None)
        elapsed=max(0,time.monotonic()-context['started']) if context and context['id']==sid else None
        with self.connect() as db:
            db.execute("UPDATE sessions SET status=?,finished_at=?,duration_seconds=COALESCE(?,duration_seconds),detail=?,updated_at=? WHERE id=? AND status='running'",(status,stamp(),elapsed,safe_text(detail),stamp(),sid))

    def _record_event(self,db,row,status,detail):
        context=getattr(self._history_local,'context',None)
        at=stamp()
        if context:
            sid=context['id']
            db.execute('UPDATE sessions SET duration_seconds=?,updated_at=? WHERE id=?',(max(0,time.monotonic()-context['started']),at,sid))
        else:
            sid=uuid.uuid4().hex
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid,'operation',row['source'],at,at,'completed',0,1,1,0,'',at))
        db.execute('INSERT INTO events(product_id,at,status,detail,ad_id,url,photo_count,session_id,product_data,source_file) VALUES(?,?,?,?,?,?,?,?,?,?)',(row['id'],at,status,detail,row['ad_id'],row['url'],row['photo_count'],sid,row['data'],row['source']))
        checking=(context and context['kind']=='check') or status in ('exists','missing','review','unchecked','invalid')
        check_result=status if checking else ''
        transfer_result='' if checking else status
        db.execute('''INSERT INTO session_items VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id,product_id) DO UPDATE SET
            check_result=CASE WHEN excluded.check_result<>'' THEN excluded.check_result ELSE session_items.check_result END,
            transfer_result=CASE WHEN excluded.transfer_result<>'' THEN excluded.transfer_result ELSE session_items.transfer_result END,
            ad_id=excluded.ad_id,url=excluded.url,photo_count=excluded.photo_count,error=excluded.error,updated_at=excluded.updated_at''',
            (sid,row['id'],row['data'],row['source'],check_result,transfer_result,row['ad_id'],row['url'],row['photo_count'],detail,at))

    def history(self,session_id=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT e.*,COALESCE(e.product_data,p.data,'{}') data FROM events e LEFT JOIN products p ON e.product_id=p.id "+('WHERE e.session_id=? ' if session_id else '')+'ORDER BY e.id DESC',(session_id,) if session_id else ())]

    def sessions(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT s.*,
              (SELECT COUNT(*) FROM session_items i WHERE i.session_id=s.id AND i.transfer_result='created') created,
              (SELECT COUNT(*) FROM session_items i WHERE i.session_id=s.id AND i.check_result='exists') existing,
              (SELECT COUNT(*) FROM session_items i WHERE i.session_id=s.id AND (i.transfer_result IN ('error','unknown') OR i.check_result='invalid')) errors,
              (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id) event_count
              FROM sessions s ORDER BY started_at DESC,id DESC''')]

    def session_items(self,session_id=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM session_items'+(' WHERE session_id=?' if session_id else '')+' ORDER BY updated_at DESC',(session_id,) if session_id else ())]

    def history_revision(self):
        with self.connect() as db:
            return tuple(db.execute('SELECT (SELECT COALESCE(MAX(id),0) FROM events),(SELECT COUNT(*) FROM sessions),(SELECT MAX(updated_at) FROM sessions),(SELECT COUNT(*) FROM events)').fetchone())


def export_history(store,path,session_id=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    wb=Workbook()
    summary=wb.active
    summary.title='Сеансы'
    summary.append(['Сеанс','Тип','Excel','Начало UTC','Окончание UTC','Статус','Всего в файле','В очереди','Было ранее','Найдены существующие','Создано','Ошибки','Длительность, сек.','Примечание'])
    for s in store.sessions():
        if session_id and s['id']!=session_id:continue
        summary.append([s['id'],s['kind'],s['source_file'],s['started_at'],s['finished_at'],s['status'],s['source_total'],s['queued_total'],s['existing_before'],s['existing'],s['created'],s['errors'],s['duration_seconds'],s['detail']])
    items=wb.create_sheet('Товары сеансов')
    items.append(['Сеанс','Excel','Название','Артикул','Проверка','Перенос','ID Авито','Ссылка','Фото','Причина','Дата UTC'])
    for r in store.session_items(session_id):
        data=json.loads(r['product_data'])
        items.append([r['session_id'],r['source_file'],data.get('title'),data.get('article'),r['check_result'],r['transfer_result'],r['ad_id'],r['url'],r['photo_count'],r['error'],r['updated_at']])
    events=wb.create_sheet('Все операции')
    events.append(['Дата UTC','Сеанс','Excel','Название','Артикул','Результат','Причина','ID Авито','Ссылка','Фото'])
    for r in store.history(session_id):
        data=json.loads(r['data'])
        events.append([r['at'],r['session_id'],r['source_file'],data.get('title'),data.get('article'),r['status'],r['detail'],r['ad_id'],r['url'],r['photo_count']])
    for sheet in wb:
        sheet.freeze_panes='A2'
        sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='234875')
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width=min(70,max(13,max(len(str(c.value or '')) for c in column)+2))
            for cell in column:
                if isinstance(cell.value,str):cell.data_type='s'
    wb.save(Path(path))
