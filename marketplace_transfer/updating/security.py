from pathlib import Path
import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_INSTALLER=512*1024*1024
ASSET='MarketplaceTransfer_Setup.exe'
MANIFEST='MarketplaceTransfer-update.json'


class UpdateError(RuntimeError):pass


def canonical(payload):
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def load_json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise UpdateError('Повторяющиеся поля в метаданных обновления')
            result[key]=value
        return result
    try:return json.loads(data,object_pairs_hook=unique)
    except (ValueError,TypeError) as exc:raise UpdateError('Некорректные метаданные обновления') from exc


def load_config(directory=None):
    config=load_json(Path(__file__).with_name('update_config.json').read_bytes())
    if directory:
        path=Path(directory)/'transfer.sqlite3'
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute('SELECT value FROM settings WHERE key=?',('update_repository',)).fetchone()
            if row and json.loads(row[0]):config['repository']=repository(json.loads(row[0]))
    return config


def parse_repository(value):
    value=value.strip()
    if '://' not in value:return repository(value)
    url=urlsplit(value)
    if url.scheme!='https' or url.hostname!='github.com' or url.username or url.password or url.query or url.fragment or url.port not in (None,443):
        raise UpdateError('Укажите обычный HTTPS-адрес репозитория GitHub без дополнительных параметров')
    return repository(url.path.strip('/').removesuffix('.git'))


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):
        raise UpdateError('Неподдерживаемый номер версии обновления')
    return tuple(int(x) for x in value.split('.'))


def repository(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',value):
        raise UpdateError('Источник обновлений GitHub ещё не настроен')
    if any(part in ('.','..') for part in value.split('/')):raise UpdateError('Некорректный репозиторий')
    return value


def asset_url(repo,tag,name):
    repository(repo)
    version(tag.removeprefix('v'))
    if tag!='v'+tag.removeprefix('v') or name not in (ASSET,MANIFEST):raise UpdateError('Недопустимый адрес файла обновления')
    return f'https://github.com/{repo}/releases/download/{tag}/{name}'


def verify_document(document,config):
    try:
        if set(document)!= {'payload','key_id','signature'}:raise ValueError()
        public=config['public_keys'][document['key_id']]
        key=Ed25519PublicKey.from_public_bytes(base64.b64decode(public,validate=True))
        key.verify(base64.b64decode(document['signature'],validate=True),canonical(document['payload']))
        return document['payload']
    except (KeyError,TypeError,ValueError,InvalidSignature) as exc:
        raise UpdateError('Цифровая подпись обновления недействительна или ключ не является доверенным') from exc


def validate_payload(payload,config,current,db_schema,online=True):
    try:
        if payload['schema']!=1 or payload['app']!='marketplace-transfer' or payload['platform']!='windows-x64':raise ValueError()
        target=version(payload['version'])
        if target<=version(current):raise UpdateError('Обновление не новее установленной версии')
        if version(current)<version(payload['min_app_version']):raise UpdateError('Нужна промежуточная версия программы')
        if not payload['min_database_schema']<=db_schema<=payload['max_database_schema']:raise UpdateError('Обновление несовместимо с текущей базой')
        if type(payload['target_database_schema']) is not int or payload['target_database_schema']<db_schema:raise UpdateError('Понижение версии базы запрещено')
        asset=payload['asset']
        if asset['name']!=ASSET or type(asset['size']) is not int or not 0<asset['size']<=MAX_INSTALLER:raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{64}',asset['sha256']):raise ValueError()
        if not isinstance(payload['notes'],str) or len(payload['notes'])>30000:raise ValueError()
        if online:
            repo=repository(config.get('repository'))
            if payload['repository']!=repo or payload['url']!=asset_url(repo,'v'+payload['version'],ASSET):raise UpdateError('Адрес обновления не соответствует закреплённому репозиторию')
        return payload
    except (KeyError,TypeError,ValueError) as exc:raise UpdateError('Неполные или несовместимые метаданные обновления') from exc


def verify_file(path,payload):
    path=Path(path)
    asset=payload['asset']
    if not path.is_file() or path.stat().st_size!=asset['size']:raise UpdateError('Размер установщика не совпал с подписанным значением')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=asset['sha256']:raise UpdateError('SHA-256 установщика не совпал; установка запрещена')
    return path
