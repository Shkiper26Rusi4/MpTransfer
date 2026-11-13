"""Separate GUI-subsystem update process, copied outside the installation before use."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
from marketplace_transfer.bridge import data_dir
from marketplace_transfer.updating.install import perform_update,rollback,installed_directory,validate_job
from marketplace_transfer.updating.locking import UpdateLock,app_is_running
from marketplace_transfer.updating.security import load_config,load_json,verify_document,verify_file


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',type=Path)
    parser.add_argument('--recover-latest',action='store_true')
    parser.add_argument('--verify-installer',type=Path)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--verification-report',type=Path)
    args=parser.parse_args()
    directory=data_dir()
    def launch(path):subprocess.Popen([str(path)],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        if args.verify_installer:
            import json
            if not args.manifest or not args.verification_report:raise RuntimeError('Укажите манифест и путь отчёта проверки')
            document=load_json(args.manifest.read_bytes())
            payload=verify_document(document,load_config())
            verify_file(args.verify_installer,payload)
            args.verification_report.write_text(json.dumps({'signature_and_file_verified':True,'version':payload['version'],'repository':payload.get('repository'),'installation_performed':False},ensure_ascii=False,indent=2),encoding='utf-8')
        elif args.recover_latest:
            if app_is_running():raise RuntimeError('Сначала закройте Marketplace Transfer; восстановление не прервёт перенос')
            jobs=sorted((directory/'updates/jobs').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            selected=next((p for p in jobs if validate_job(p,directory,installed_directory()).get('phase') in ('backed_up','installed')),None)
            if not selected:raise RuntimeError('Нет незавершённого обновления для восстановления')
            job=validate_job(selected,directory,installed_directory())
            verify_document(job['document'],load_config())
            with UpdateLock(directory/'updates'):rollback(selected,directory,installed_directory())
            launch(installed_directory()/'MarketplaceTransfer.exe')
        elif args.job:
            perform_update(args.job,directory,launcher=launch)
        else:raise RuntimeError('Не указано задание обновления')
    except Exception as exc:
        if args.verification_report:
            import json
            args.verification_report.write_text(json.dumps({'signature_and_file_verified':False,'error':str(exc),'installation_performed':False},ensure_ascii=False,indent=2),encoding='utf-8')
        elif os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc),'Обновление Marketplace Transfer',0x10)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
