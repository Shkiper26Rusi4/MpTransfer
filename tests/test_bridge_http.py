import httpx
from marketplace_transfer.bridge import Bridge


def test_http_auth_and_readonly_status(tmp_path):
    bridge=Bridge(tmp_path)
    base=f'http://127.0.0.1:{bridge.server.server_port}'
    try:
        with httpx.Client(trust_env=False) as client:
            assert client.post(base+'/status',json={}).status_code==403
            headers={'Authorization':'Bearer '+bridge.token}
            result=client.post(base+'/status',json={},headers=headers)
            assert result.json()=={'connected':False,'extension':None}
            # Merely reading status must not turn a disconnected browser into connected.
            assert not bridge.connected
            assert client.post(base+'/status',json={},headers={**headers,'Origin':'https://www.avito.ru'}).status_code==403
            assert client.post(base+'/status',json=[],headers=headers).status_code==400
            assert client.post(base+'/diagnose',json={},headers=headers).json().get('error')
    finally:
        bridge.close()
