from pathlib import Path
from urllib.parse import urljoin,urlsplit
import hashlib
import os
import uuid
import httpx
from .security import ASSET,MANIFEST,UpdateError,asset_url,load_json,repository,validate_payload,verify_document,verify_file,version

CDN_HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}


def safe_redirect(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in CDN_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:
        raise UpdateError('Недопустимое перенаправление загрузки обновления')


class ReleaseFeed:
    def __init__(self,config,current,db_schema,transport=None):
        self.config,self.current,self.db_schema=config,current,db_schema
        self.transport=transport

    def client(self):
        return httpx.Client(timeout=httpx.Timeout(30,connect=15),follow_redirects=False,transport=self.transport,headers={'User-Agent':'MarketplaceTransfer/'+self.current},verify=True)

    def stream(self,client,url):
        initial=url
        for attempt in range(5):
            safe_redirect(url)
            response=client.send(client.build_request('GET',url),stream=True)
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('location')
                response.close()
                if not location:raise UpdateError('Пустое перенаправление обновления')
                url=urljoin(url,location)
                safe_redirect(url)
                if urlsplit(url).hostname=='github.com' and url!=initial:raise UpdateError('Репозиторий перенаправил загрузку на другой путь')
                continue
            response.raise_for_status()
            return response
        raise UpdateError('Слишком много перенаправлений обновления')

    def check(self):
        repo=repository(self.config.get('repository'))
        with self.client() as client:
            response=client.get(f'https://api.github.com/repos/{repo}/releases/latest',headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
            if response.status_code==404:raise UpdateError('Публичный релиз GitHub ещё не опубликован или недоступен')
            response.raise_for_status()
            if len(response.content)>2*1024*1024:raise UpdateError('Слишком большой ответ GitHub')
            release=load_json(response.content)
            if release.get('draft') or release.get('prerelease'):raise UpdateError('Предварительный релиз не устанавливается автоматически')
            tag=release.get('tag_name','')
            remote=version(tag.removeprefix('v'))
            if remote<=version(self.current):return None
            assets={}
            for item in release.get('assets',[]):
                if item.get('name') in assets:raise UpdateError('Неоднозначные файлы релиза')
                assets[item.get('name')]=item
            for name in (MANIFEST,ASSET):
                if assets.get(name,{}).get('browser_download_url')!=asset_url(repo,tag,name):raise UpdateError('Файлы релиза отсутствуют или имеют неожиданный адрес')
            response=self.stream(client,asset_url(repo,tag,MANIFEST))
            try:
                chunks=[]
                size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>128*1024:raise UpdateError('Метаданные обновления слишком велики')
                    chunks.append(chunk)
                document=load_json(b''.join(chunks))
            finally:response.close()
        payload=validate_payload(verify_document(document,self.config),self.config,self.current,self.db_schema)
        if tag!='v'+payload['version'] or assets[ASSET].get('size')!=payload['asset']['size']:raise UpdateError('Подписанная версия или размер не совпали с релизом GitHub')
        return document

    def download(self,document,directory,progress=lambda value:None,cancel=None):
        payload=validate_payload(verify_document(document,self.config),self.config,self.current,self.db_schema)
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        destination=directory/(payload['asset']['sha256']+'.exe')
        if destination.exists():return verify_file(destination,payload)
        partial=directory/(uuid.uuid4().hex+'.part')
        expected=payload['asset']['size']
        try:
            with self.client() as client:
                response=self.stream(client,payload['url'])
                try:
                    length=response.headers.get('content-length')
                    if length and int(length)!=expected:raise UpdateError('Сервер вернул другой размер обновления')
                    downloaded=0
                    with partial.open('xb') as stream:
                        for chunk in response.iter_bytes(256*1024):
                            if cancel and cancel.is_set():raise UpdateError('Загрузка отменена')
                            downloaded+=len(chunk)
                            if downloaded>expected:raise UpdateError('Файл обновления превышает подписанный размер')
                            stream.write(chunk)
                            progress(int(downloaded*100/expected))
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:response.close()
            verify_file(partial,payload)
            os.replace(partial,destination)
            return destination
        finally:
            partial.unlink(missing_ok=True)
