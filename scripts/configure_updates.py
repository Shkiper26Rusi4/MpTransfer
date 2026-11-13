from pathlib import Path
from urllib.parse import urlsplit
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marketplace_transfer.updating.security import repository

if len(sys.argv)!=2:raise SystemExit('Usage: python scripts/configure_updates.py https://github.com/OWNER/REPOSITORY')
url=urlsplit(sys.argv[1])
if url.scheme!='https' or url.hostname!='github.com' or url.username or url.password or url.query or url.fragment or url.port not in (None,443):
    raise SystemExit('Требуется обычный HTTPS-адрес репозитория GitHub')
repo=repository(url.path.strip('/').removesuffix('.git'))
path=Path(__file__).resolve().parents[1]/'marketplace_transfer/updating/update_config.json'
config=json.loads(path.read_text(encoding='utf-8'))
config['repository']=repo
path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
print('Источник настроен:',repo,'— пересоберите программу и установщик')
