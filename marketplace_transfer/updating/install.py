from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from .locking import UpdateLock,app_is_running
from .security import UpdateError,load_config,load_json,verify_document,validate_payload,verify_file,version

APP='MarketplaceTransfer.exe'
HELPER='MarketplaceTransferUpdater.exe'
UNINSTALL_KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A8768EE5-DC4F-46A4-A9EC-95AF4DA56645}_is1'


def installed_directory():
    return Path(os.environ['LOCALAPPDATA'])/'Programs/MarketplaceTransferNew'


def atomic_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        with temp.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def data_signature(path,event_through=None):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT id,fingerprint,ad_id,url FROM products ORDER BY id').fetchall()
        events=db.execute('SELECT id,product_id,at,status,detail,ad_id,url,photo_count FROM events'+(' WHERE id<=?' if event_through is not None else '')+' ORDER BY id',(event_through,) if event_through is not None else ()).fetchall()
        return {'products':len(rows),'links_sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                'events':db.execute('SELECT COUNT(*) FROM events').fetchone()[0],'last_event_id':events[-1][0] if events else 0,
                'events_sha256':hashlib.sha256(json.dumps(events,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                'schema':db.execute('PRAGMA user_version').fetchone()[0]}


def prepare_job(document,installer,directory,app_directory,current,config=None,expected_install=None):
    config=config or load_config(directory)
    directory=Path(directory).resolve()
    app_directory=Path(app_directory).resolve()
    expected=Path(expected_install or installed_directory()).resolve()
    if app_directory!=expected:raise UpdateError('Автообновление доступно для установленной версии. Установите программу через Setup.')
    payload=validate_payload(verify_document(document,config),config,current,data_signature(directory/'transfer.sqlite3')['schema'])
    verify_file(installer,payload)
    job_dir=directory/'updates/jobs'/uuid.uuid4().hex
    job_dir.mkdir(parents=True)
    shutil.copy2(installer,job_dir/'installer.exe')
    verify_file(job_dir/'installer.exe',payload)
    shutil.copy2(app_directory/HELPER,job_dir/HELPER)
    job={'document':document,'parent_pid':os.getpid(),'phase':'prepared','created_at':datetime.now(timezone.utc).isoformat(),
         'app_directory':str(app_directory),'current_version':current}
    atomic_json(job_dir/'job.json',job)
    return job_dir/'job.json'


def validate_job(path,directory,expected_install):
    path=Path(path).resolve()
    root=(Path(directory)/'updates/jobs').resolve()
    try:parts=path.relative_to(root).parts
    except ValueError as exc:raise UpdateError('Задание обновления находится вне папки обновлений') from exc
    if len(parts)!=2 or parts[1]!='job.json' or len(parts[0])!=32 or any(c not in '0123456789abcdef' for c in parts[0]):raise UpdateError('Некорректное задание обновления')
    job=load_json(path.read_bytes())
    if Path(job['app_directory']).resolve()!=Path(expected_install).resolve():raise UpdateError('Нельзя менять каталог установки через задание обновления')
    return job


def pending_recovery(directory,expected_install=None):
    expected=Path(expected_install or installed_directory()).resolve()
    paths=sorted((Path(directory)/'updates/jobs').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    for path in paths:
        try:job=validate_job(path,directory,expected)
        except (OSError,ValueError,KeyError,UpdateError):continue
        if job.get('phase') in ('backed_up','installed'):return path
    return None


def wait_parent(pid,timeout=90):
    if type(pid) is not int or pid<=0:raise UpdateError('Некорректный процесс приложения')
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle:return
    try:
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:raise UpdateError('Программа не завершилась; обновление отложено')
    finally:
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle(handle)


def run_hidden(arguments,timeout=300):
    return subprocess.run([str(x) for x in arguments],timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,capture_output=True)


def health_check(app,directory,report):
    result=run_hidden([Path(app)/APP,'--health-check',report,'--data-dir',directory],timeout=90)
    if result.returncode!=0 or not Path(report).is_file():raise UpdateError('Проверка запуска установленной программы не пройдена')
    return load_json(Path(report).read_bytes())


def registry_snapshot():
    if os.name!='nt':return {}
    import winreg
    result={}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,UNINSTALL_KEY) as key:
            index=0
            while True:
                try:
                    name,value,kind=winreg.EnumValue(key,index)
                    if isinstance(value,(str,int)):result[name]=[value,kind]
                    index+=1
                except OSError:break
    except OSError:pass
    return result


def restore_registry(values):
    if os.name!='nt' or not values:return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,UNINSTALL_KEY) as key:
        for name,(value,kind) in values.items():winreg.SetValueEx(key,name,0,kind,value)


def rollback(job_path,directory,expected_install):
    if app_is_running():raise UpdateError('Закройте Marketplace Transfer перед восстановлением; данные не изменены')
    job=validate_job(job_path,directory,expected_install)
    root=Path(job_path).resolve().parent
    backup=root/'rollback'
    app=Path(expected_install).resolve()
    if not (backup/'app'/APP).is_file() or not (backup/'transfer.sqlite3').is_file():raise UpdateError('Резервная копия обновления неполная; автоматический откат запрещён')
    # Both resolved targets are checked above. Failed files are retained, never deleted.
    if app.exists():os.replace(app,root/('failed-app-'+uuid.uuid4().hex))
    shutil.copytree(backup/'app',app)
    database=Path(directory)/'transfer.sqlite3'
    failed=root/('failed-data-'+uuid.uuid4().hex)
    failed.mkdir()
    # SQLite's backup API restores a consistent database without removing WAL,
    # images or user files. Explicit closing is essential on Windows.
    with closing(sqlite3.connect(database)) as src,closing(sqlite3.connect(failed/'transfer.sqlite3')) as dst:src.backup(dst)
    with closing(sqlite3.connect(backup/'transfer.sqlite3')) as src,closing(sqlite3.connect(database)) as dst:src.backup(dst)
    restore_registry(job.get('registry_before',{}))
    job['phase']='rolled_back'
    atomic_json(job_path,job)
    return job


def perform_update(job_path,directory,expected_install=None,config=None,installer_runner=None,health_runner=None,wait=True,launcher=None):
    directory=Path(directory).resolve()
    app=Path(expected_install or installed_directory()).resolve()
    config=config or load_config(directory)
    job=validate_job(job_path,directory,app)
    root=Path(job_path).resolve().parent
    document=job['document']
    payload=verify_document(document,config)
    verify_file(root/'installer.exe',payload)
    if wait:wait_parent(job['parent_pid'])
    receipt={'version':payload['version'],'started_at':datetime.now(timezone.utc).isoformat()}
    health_runner=health_runner or health_check
    with UpdateLock(directory/'updates'):
        try:
            if app_is_running():raise UpdateError('Программа ещё работает; обновление не будет её прерывать')
            before=health_runner(app,directory,root/'before-health.json')
            validate_payload(payload,config,before['version'],before['schema'])
            highest=None
            with closing(sqlite3.connect((directory/'transfer.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute('SELECT value FROM settings WHERE key=?',('highest_update_version',)).fetchone()
                if row:highest=json.loads(row[0])
            if highest and version(payload['version'])<=version(highest):raise UpdateError('Повторная установка старого обновления запрещена')
            backup=root/'rollback'
            backup.mkdir()
            shutil.copytree(app,backup/'app')
            with closing(sqlite3.connect(directory/'transfer.sqlite3')) as src,closing(sqlite3.connect(backup/'transfer.sqlite3')) as dst:src.backup(dst)
            job.update({'phase':'backed_up','before_signature':data_signature(directory/'transfer.sqlite3'),'registry_before':registry_snapshot()})
            atomic_json(job_path,job)
            if installer_runner:
                code=installer_runner(root/'installer.exe',app)
            else:
                result=run_hidden([root/'installer.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS','/SP-', '/CURRENTUSER','/DIR='+str(app),'/LOG='+str(root/'setup.log')],timeout=900)
                code=result.returncode
            if code!=0:raise UpdateError(f'Установщик завершился с кодом {code}')
            job['phase']='installed'
            atomic_json(job_path,job)
            after=health_runner(app,directory,root/'after-health.json')
            signature=data_signature(directory/'transfer.sqlite3',job['before_signature']['last_event_id'])
            if after['version']!=payload['version'] or after['schema']!=payload['target_database_schema'] or signature['links_sha256']!=job['before_signature']['links_sha256'] or signature['events']<job['before_signature']['events'] or signature['events_sha256']!=job['before_signature']['events_sha256']:
                raise UpdateError('После обновления версия или сохранённые связи не прошли проверку')
            with closing(sqlite3.connect(directory/'transfer.sqlite3')) as db,db:
                db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('highest_update_version',json.dumps(payload['version'])))
            job['phase']='completed'
            atomic_json(job_path,job)
            receipt['status']='completed'
        except Exception as exc:
            receipt.update({'status':'failed','error':str(exc)})
            if job.get('phase') in ('backed_up','installed'):
                try:
                    rollback(job_path,directory,app)
                    receipt['status']='rolled_back'
                except Exception as recovery:
                    receipt['recovery_error']=str(recovery)
            atomic_json(directory/'updates/last-result.json',receipt)
            if receipt['status']=='failed':raise UpdateError('Обновление не установлено: '+str(exc)) from exc
        atomic_json(directory/'updates/last-result.json',receipt)
    if launcher:launcher(app/APP)
    return receipt
