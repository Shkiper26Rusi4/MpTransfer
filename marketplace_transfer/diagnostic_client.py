"""Read-only local diagnostic client for the running GUI bridge."""
import json
from pathlib import Path
import urllib.request
from .bridge import data_dir, BridgeError


class DiagnosticClient:
    def __init__(self, directory=None):
        self.directory=Path(directory or data_dir())

    def call(self, command, payload=None, timeout=40):
        if command != 'diagnose':
            raise BridgeError('Diagnostic client only supports the inspected page protocol')
        descriptor=json.loads((self.directory/'bridge.json').read_text(encoding='utf-8'))
        request=urllib.request.Request(f'http://127.0.0.1:{int(descriptor["port"])}/diagnose',data=json.dumps(payload or {}).encode(),headers={'Authorization':'Bearer '+descriptor['token'],'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=timeout) as response:
            result=json.load(response)
        if result.get('error'):
            raise BridgeError(result['error'])
        return result
