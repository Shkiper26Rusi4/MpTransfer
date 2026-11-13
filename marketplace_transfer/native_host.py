import json
import os
import struct
import sys
import threading
import urllib.request
from .bridge import data_dir


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size - len(result))
        if not part:
            raise EOFError
        result.extend(part)
    return bytes(result)


def read_message(stream):
    length = struct.unpack("<I", read_exact(stream, 4))[0]
    if length > 24 * 1024 * 1024:
        raise ValueError("Native message too large")
    return json.loads(read_exact(stream, length))


def write_message(stream, message):
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > 1000000:
        raise ValueError("Native response too large")
    stream.write(struct.pack("<I", len(payload)) + payload)
    stream.flush()


def main():
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        descriptor = json.loads((data_dir() / "bridge.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # stdout is exclusively the Native Messaging channel.
        sys.stderr.write("Marketplace Transfer: запустите локальное приложение перед подключением расширения.\n")
        return
    def post(path, obj):
        request = urllib.request.Request(f'http://127.0.0.1:{int(descriptor["port"])}{path}', data=json.dumps(obj).encode(), headers={"Authorization": "Bearer " + descriptor["token"], "Content-Type": "application/json"})
        # Never route local bridge through environment proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=25) as response:
            return json.load(response)

    def receive():
        try:
            while True:
                post("/result", read_message(sys.stdin.buffer))
        except (EOFError, OSError, ValueError):
            os._exit(0)
    threading.Thread(target=receive, daemon=True).start()
    try:
        while True:
            write_message(sys.stdout.buffer, post("/poll", {}))
    except (OSError, ValueError):
        return

if __name__ == "__main__":
    main()
