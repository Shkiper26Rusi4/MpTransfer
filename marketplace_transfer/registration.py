import json
from pathlib import Path
import re
import sys
import base64
import hashlib


def bundled_extension_id(base):
    manifest = json.loads((Path(base) / "extension" / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(base64.b64decode(manifest["key"], validate=True)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def register(extension_id, directory, host_path=None):
    if sys.platform != "win32":
        raise RuntimeError("Регистрация поддерживается в Windows")
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("Укажите ID расширения: 32 буквы a–p из chrome://extensions")
    import winreg
    if host_path is not None:
        host = Path(host_path).resolve()
    elif getattr(sys, "frozen", False):
        host = Path(sys.executable).parent / "MarketplaceTransferHost.exe"
    else:
        raise RuntimeError("Для регистрации используйте собранную программу из outputs/MarketplaceTransfer. Запуск хоста Chrome требует отдельного EXE.")
    if not host.is_file():
        raise FileNotFoundError(f"Не найден Native Host: {host}")
    manifest = Path(directory) / "com.marketplace.transfer.json"
    manifest.write_text(json.dumps({"name": "com.marketplace.transfer", "description": "Marketplace Transfer native bridge", "path": str(host), "type": "stdio", "allowed_origins": [f"chrome-extension://{extension_id}/"]}, indent=2), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\NativeMessagingHosts\com.marketplace.transfer") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
