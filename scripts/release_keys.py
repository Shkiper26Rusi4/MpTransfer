"""Local release signing. Private material stays DPAPI-encrypted outside the project."""
from pathlib import Path
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import subprocess
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def key_path():
    return Path(os.environ['LOCALAPPDATA'])/'MarketplaceTransferSigning/signing-key.dpapi'


def dpapi(data,protect):
    if sys.platform!='win32':raise RuntimeError('Локальный ключ подписи защищается Windows DPAPI')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result=Blob()
    crypto=ctypes.WinDLL('crypt32',use_last_error=True)
    function=crypto.CryptProtectData if protect else crypto.CryptUnprotectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):raise ctypes.WinError(ctypes.get_last_error())
    free=ctypes.WinDLL('kernel32',use_last_error=True).LocalFree
    free.argtypes=[ctypes.c_void_p]
    free.restype=ctypes.c_void_p
    try:return ctypes.string_at(result.data,result.size)
    finally:free(result.data)


def load_private():
    return Ed25519PrivateKey.from_private_bytes(dpapi(key_path().read_bytes(),False))


def generate():
    path=key_path()
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        user=os.environ.get('USERDOMAIN','')+'\\'+os.environ['USERNAME']
        subprocess.run(['icacls',str(path.parent),'/inheritance:r','/grant:r',user+':(OI)(CI)F','/grant:r','*S-1-5-18:(OI)(CI)F'],check=True,capture_output=True)
        private=Ed25519PrivateKey.generate()
        raw=private.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        protected=dpapi(raw,True)
        with path.open('xb') as stream:stream.write(protected)
    private=load_private()
    public=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    key_id=hashlib.sha256(public).hexdigest()[:16]
    target=Path(__file__).resolve().parents[1]/'marketplace_transfer/updating/update_config.json'
    config=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {'repository':None,'public_keys':{}}
    config['public_keys'][key_id]=base64.b64encode(public).decode('ascii')
    config['signing_key_id']=key_id
    target.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Public trust key pinned:',key_id)
    print('Private key: Windows DPAPI, outside project and installers')


if __name__=='__main__':generate()
