"""Read-only diagnostic through the app's Native Messaging bridge, never Chrome storage."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marketplace_transfer.bridge import data_dir


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--operation", choices=["inspect","tab","scroll","navigate","category"], default="inspect")
    parser.add_argument("--label")
    parser.add_argument("--marker")
    parser.add_argument("--path")
    args=parser.parse_args()
    try:
        descriptor=json.loads((data_dir()/"bridge.json").read_text(encoding="utf-8"))
        path="/status" if args.status else "/diagnose"
        payload={"operation":args.operation,"marker":args.marker,"path":args.path,"label":args.label}
        request=urllib.request.Request(f'http://127.0.0.1:{int(descriptor["port"])}{path}', data=json.dumps(payload).encode(), headers={"Authorization":"Bearer "+descriptor["token"],"Content-Type":"application/json"})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=35) as response:
            result=json.load(response)
        text=json.dumps(result,ensure_ascii=False,indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(text,encoding="utf-8")
        else:
            print(text)
    except (OSError,ValueError) as exc:
        print("Диагностика недоступна: "+str(exc),file=sys.stderr)
        return 1
    return 0

if __name__=="__main__":
    sys.exit(main())
