"""Authenticated loopback transport. Native host is the only Chrome-facing process."""
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def data_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MarketplaceTransferNew"


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, directory=None):
        self.directory = Path(directory or data_dir())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.pending = queue.Queue()
        self.waiters = {}
        self.lock = threading.Lock()
        self.last_seen = 0
        self.extension = None
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + bridge.token or self.headers.get("Origin"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    self.send_error(400)
                    return
                if not 0 < length <= 24 * 1024 * 1024:
                    self.send_error(413)
                    return
                try:
                    request = json.loads(self.rfile.read(length))
                    if not isinstance(request, dict):
                        self.send_error(400)
                        return
                    if self.path == "/poll":
                        bridge.last_seen = time.monotonic()
                        try:
                            response = bridge.pending.get(timeout=20)
                        except queue.Empty:
                            response = {"command": "heartbeat"}
                    elif self.path == "/result":
                        bridge.last_seen = time.monotonic()
                        if request.get("event") == "hello":
                            bridge.extension = {"id": request.get("extensionId"), "version": request.get("version"), "at": time.time()}
                        with bridge.lock:
                            waiter = bridge.waiters.get(request.get("id"))
                        if waiter:
                            try:
                                waiter.put_nowait(request)
                            except queue.Full:
                                pass
                        response = {"ok": True}
                    elif self.path == "/status":
                        response = {"connected":bridge.connected,"extension":bridge.extension}
                    elif self.path == "/diagnose":
                        try:
                            response = bridge.call("diagnose", request, timeout=30)
                        except BridgeError as exc:
                            response = {"error":str(exc)}
                    else:
                        self.send_error(404)
                        return
                    payload = json.dumps(response).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (ValueError, OSError):
                    return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.descriptor = self.directory / "bridge.json"
        self.descriptor.write_text(json.dumps({"port": self.server.server_port, "token": self.token}), encoding="utf-8")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def connected(self):
        return time.monotonic() - self.last_seen < 45

    def call(self, command, payload=None, timeout=30):
        if not self.connected:
            raise BridgeError("Расширение не подключено. Откройте его меню в Chrome и нажмите «Подключить».")
        ident = uuid.uuid4().hex
        waiter = queue.Queue(maxsize=1)
        with self.lock:
            self.waiters[ident] = waiter
        self.pending.put({"id": ident, "command": command, "payload": payload or {}, "expires": time.time() + timeout})
        try:
            response = waiter.get(timeout=timeout)
            if response.get("error"):
                raise BridgeError(response["error"])
            return response.get("result")
        except queue.Empty as exc:
            raise BridgeError("Истёк тайм-аут связи с Chrome; результат операции неизвестен") from exc
        finally:
            with self.lock:
                self.waiters.pop(ident, None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        if self.descriptor.exists():
            try:
                if json.loads(self.descriptor.read_text()).get("token") == self.token:
                    self.descriptor.unlink()
            except OSError:
                pass
