import base64
import hashlib
import json
from pathlib import Path
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from marketplace_transfer.updating.security import canonical,verify_document,validate_payload,verify_file,UpdateError,asset_url
from marketplace_transfer.updating.feed import ReleaseFeed
from marketplace_transfer.updating.locking import UpdateLock
from marketplace_transfer.updating.install import prepare_job,perform_update,validate_job
from marketplace_transfer.storage import Store
from marketplace_transfer.domain import Product


@pytest.fixture
def signed():
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    config={'repository':'test-owner/test-repo','public_keys':{'test':base64.b64encode(public).decode()}}
    content=b'Local installer contract fixture; never executed'
    payload={'schema':1,'app':'marketplace-transfer','platform':'windows-x64','version':'0.5.0','min_app_version':'0.2.2',
             'min_database_schema':1,'max_database_schema':3,'target_database_schema':3,'repository':config['repository'],
             'url':asset_url(config['repository'],'v0.5.0','MarketplaceTransfer_Setup.exe'),
             'asset':{'name':'MarketplaceTransfer_Setup.exe','size':len(content),'sha256':hashlib.sha256(content).hexdigest()},'notes':'Test fixture'}
    def sign(value):return {'key_id':'test','payload':value,'signature':base64.b64encode(key.sign(canonical(value))).decode()}
    return config,payload,sign,content


def test_pinned_signature_rejects_modified_and_unknown_signers(signed):
    config,payload,sign,_=signed
    document=sign(payload)
    assert verify_document(document,config)==payload
    altered=json.loads(json.dumps(document));altered['payload']['version']='9.9.9'
    with pytest.raises(UpdateError):verify_document(altered,config)
    with pytest.raises(UpdateError):verify_document({'payload':payload},config)
    document['key_id']='attacker'
    with pytest.raises(UpdateError):verify_document(document,config)


@pytest.mark.parametrize('changes',[
    {'version':'0.4.0'},{'min_app_version':'1.0.0'},{'max_database_schema':2},
    {'platform':'linux-x64'},{'repository':'attacker/repo'},{'url':'http://github.com/test-owner/test-repo/file'},
    {'url':'https://github.com.evil.test/file'},{'asset':{'name':'other.exe','size':1,'sha256':'0'*64}},
])
def test_version_schema_platform_and_url_validation(signed,changes):
    config,payload,_,_=signed
    with pytest.raises(UpdateError):validate_payload(dict(payload,**changes),config,'0.4.0',3)


def test_size_and_sha256_checked_independently(signed,tmp_path):
    _,payload,_,content=signed
    path=tmp_path/'installer.exe'
    path.write_bytes(content)
    assert verify_file(path,payload)==path
    path.write_bytes(b'x'*len(content))
    with pytest.raises(UpdateError,match='SHA-256'):verify_file(path,payload)
    path.write_bytes(content+b'x')
    with pytest.raises(UpdateError,match='Размер'):verify_file(path,payload)


def transport_for(signed,redirect=None,body=None):
    config,payload,sign,content=signed
    document=sign(payload)
    def handle(request):
        if request.url.host=='api.github.com':
            return httpx.Response(200,json={'tag_name':'v0.5.0','draft':False,'prerelease':False,'assets':[
                {'name':'MarketplaceTransfer-update.json','size':1000,'browser_download_url':asset_url(config['repository'],'v0.5.0','MarketplaceTransfer-update.json')},
                {'name':'MarketplaceTransfer_Setup.exe','size':len(content),'browser_download_url':payload['url']}]})
        if request.url.path.endswith('MarketplaceTransfer-update.json'):return httpx.Response(200,json=document)
        if redirect and request.url.host=='github.com':return httpx.Response(302,headers={'location':redirect})
        return httpx.Response(200,content=content if body is None else body)
    return httpx.MockTransport(handle)


def test_signed_feed_download_through_allowed_github_cdn(signed,tmp_path):
    config,payload,_,content=signed
    feed=ReleaseFeed(config,'0.4.0',3,transport_for(signed,'https://release-assets.githubusercontent.com/asset?temporary=opaque'))
    document=feed.check()
    progress=[]
    path=feed.download(document,tmp_path,progress.append)
    assert path.read_bytes()==content and progress[-1]==100
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('url',['http://release-assets.githubusercontent.com/file','https://evil.test/file','https://github.com.evil.test/file','https://name:password@github.com/file'])
def test_redirect_restrictions(signed,tmp_path,url):
    config,payload,sign,_=signed
    feed=ReleaseFeed(config,'0.4.0',3,transport_for(signed,url))
    with pytest.raises(UpdateError):feed.download(sign(payload),tmp_path)
    assert not list(tmp_path.glob('*.exe')) and not list(tmp_path.glob('*.part'))


def test_corrupt_download_never_becomes_installable(signed,tmp_path):
    config,payload,sign,content=signed
    feed=ReleaseFeed(config,'0.4.0',3,transport_for(signed,body=b'x'*len(content)))
    with pytest.raises(UpdateError):feed.download(sign(payload),tmp_path)
    assert not list(tmp_path.iterdir())


def test_single_updater_lock(tmp_path):
    with UpdateLock(tmp_path):
        with pytest.raises(UpdateError):
            with UpdateLock(tmp_path):pass
    with UpdateLock(tmp_path):pass


def make_job(signed,tmp_path,monkeypatch):
    import marketplace_transfer.updating.install as install
    monkeypatch.setattr(install,'app_is_running',lambda:False)
    monkeypatch.setattr(install,'registry_snapshot',lambda:{})
    monkeypatch.setattr(install,'restore_registry',lambda values:None)
    config,payload,sign,content=signed
    app=tmp_path/'Programs/MarketplaceTransferNew';app.mkdir(parents=True)
    (app/'MarketplaceTransfer.exe').write_bytes(b'old')
    (app/'MarketplaceTransferUpdater.exe').write_bytes(b'fixture helper')
    directory=tmp_path/'Data';directory.mkdir()
    store=Store(directory/'transfer.sqlite3')
    store.import_products([Product('Existing',price='1')],'source')
    store.transition(1,'created',ad_id='555',url='https://www.avito.ru/draft/555',photo_count=8)
    store.set_setting('theme',1)
    (directory/'photo.jpg').write_bytes(b'original photograph')
    installer=tmp_path/'download.exe';installer.write_bytes(content)
    job=prepare_job(sign(payload),installer,directory,app,'0.4.0',config,expected_install=app)
    return config,app,directory,job


def test_successful_update_preserves_data_and_relaunches(signed,tmp_path,monkeypatch):
    config,app,directory,job=make_job(signed,tmp_path,monkeypatch)
    def installer(path,target):
        (target/'MarketplaceTransfer.exe').write_bytes(b'new')
        return 0
    def health(target,data,report):return {'version':'0.4.0' if (target/'MarketplaceTransfer.exe').read_bytes()==b'old' else '0.5.0','schema':3}
    launched=[]
    result=perform_update(job,directory,expected_install=app,config=config,installer_runner=installer,health_runner=health,wait=False,launcher=launched.append)
    assert result['status']=='completed' and launched==[app/'MarketplaceTransfer.exe']
    store=Store(directory/'transfer.sqlite3')
    assert store.rows()[0]['ad_id']=='555' and store.setting('theme')==1
    assert store.setting('highest_update_version')=='0.5.0'
    assert (directory/'photo.jpg').read_bytes()==b'original photograph'


@pytest.mark.parametrize('installer_failure',[True,False])
def test_failed_install_or_health_restores_app_and_database(signed,tmp_path,monkeypatch,installer_failure):
    config,app,directory,job=make_job(signed,tmp_path,monkeypatch)
    def installer(path,target):
        (target/'MarketplaceTransfer.exe').write_bytes(b'new broken')
        with __import__('sqlite3').connect(directory/'transfer.sqlite3') as db:db.execute('DELETE FROM products')
        return 7 if installer_failure else 0
    def health(target,data,report):
        if (target/'MarketplaceTransfer.exe').read_bytes()==b'old':return {'version':'0.4.0','schema':3}
        raise RuntimeError('New application failed health check')
    result=perform_update(job,directory,expected_install=app,config=config,installer_runner=installer,health_runner=health,wait=False)
    assert result['status']=='rolled_back'
    assert (app/'MarketplaceTransfer.exe').read_bytes()==b'old'
    store=Store(directory/'transfer.sqlite3')
    assert store.rows()[0]['ad_id']=='555' and len(store.history())==1
    assert (directory/'photo.jpg').exists()
    assert list(job.parent.glob('failed-data-*'))


def test_job_cannot_redirect_installation(signed,tmp_path,monkeypatch):
    config,app,directory,job=make_job(signed,tmp_path,monkeypatch)
    data=json.loads(job.read_text(encoding='utf-8'));data['app_directory']=str(tmp_path)
    job.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(UpdateError):validate_job(job,directory,app)


def test_interrupted_update_is_detected_before_new_work(signed,tmp_path,monkeypatch):
    from marketplace_transfer.updating.install import pending_recovery,atomic_json
    config,app,directory,job=make_job(signed,tmp_path,monkeypatch)
    assert pending_recovery(directory,app) is None
    state=json.loads(job.read_text(encoding='utf-8'))
    for phase in ('backed_up','installed'):
        state['phase']=phase;atomic_json(job,state)
        assert pending_recovery(directory,app)==job
    state['phase']='completed';atomic_json(job,state)
    assert pending_recovery(directory,app) is None


def test_install_waits_for_active_marketplace_worker(qtbot,tmp_path,monkeypatch):
    from marketplace_transfer.app import Window
    window=Window(tmp_path)
    qtbot.addWidget(window)
    class Busy:
        def isRunning(self):return True
    window.worker=Busy()
    window.pending_update_job=tmp_path/'job.json'
    window.install_requested=True
    def forbidden(*args,**kwargs):raise AssertionError('Active queue must not be interrupted')
    monkeypatch.setattr('marketplace_transfer.update_ui.subprocess.Popen',forbidden)
    window.try_install_update()
    assert not window._installing and window.install_requested
    window.worker=None
    window.close()


def test_user_can_configure_real_source_without_changing_trusted_keys(qtbot,tmp_path):
    from marketplace_transfer.app import Window
    window=Window(tmp_path)
    qtbot.addWidget(window)
    keys=dict(window.update_config['public_keys'])
    window.update_repository.setText('https://github.com/test-owner/test-repo')
    window.save_update_source()
    assert window.update_config['repository']=='test-owner/test-repo'
    assert window.update_config['public_keys']==keys
    errors=[]
    window.update_failed=lambda text,manual:errors.append(text)
    window.update_repository.setText('https://github.com.evil.test/not-github')
    window.save_update_source()
    assert errors and window.store.setting('update_repository')=='test-owner/test-repo'
    window.close()
