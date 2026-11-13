from pathlib import Path
import argparse
import base64
import hashlib
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from release_keys import load_private
from marketplace_transfer import __version__
from marketplace_transfer.history_schema import SCHEMA_VERSION
from marketplace_transfer.updating.security import ASSET,asset_url,canonical,load_config,verify_document,verify_file

parser=argparse.ArgumentParser()
parser.add_argument('--installer',type=Path,default=Path('outputs')/ASSET)
parser.add_argument('--output',type=Path,default=Path('outputs/MarketplaceTransfer-update.json'))
parser.add_argument('--notes',type=Path,required=True)
args=parser.parse_args()
config=load_config()
repo=config.get('repository')
payload={'schema':1,'app':'marketplace-transfer','version':__version__,'platform':'windows-x64',
         'min_app_version':'0.2.2','min_database_schema':1,'max_database_schema':SCHEMA_VERSION,'target_database_schema':SCHEMA_VERSION,
         'repository':repo,'url':asset_url(repo,'v'+__version__,ASSET) if repo else None,
         'asset':{'name':ASSET,'size':args.installer.stat().st_size,'sha256':hashlib.sha256(args.installer.read_bytes()).hexdigest()},
         'notes':args.notes.read_text(encoding='utf-8')}
document={'key_id':config['signing_key_id'],'payload':payload,'signature':base64.b64encode(load_private().sign(canonical(payload))).decode('ascii')}
verify_file(args.installer,verify_document(document,config))
args.output.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
print('Подпись, SHA-256 и размер проверены. Онлайн-источник:',repo or 'НЕ НАСТРОЕН (локальная проверка подписи)')
