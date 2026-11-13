"""Actual packaged host + Python server, simulated Chrome pipe peer. Not an Avito test."""
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import pytest
from marketplace_transfer.bridge import Bridge
from marketplace_transfer.native_host import read_message, write_message
from marketplace_transfer.registration import bundled_extension_id


def test_packaged_native_roundtrip(tmp_path):
    host = Path(__file__).resolve().parents[1] / 'outputs/MarketplaceTransfer/MarketplaceTransferHost.exe'
    if not host.exists():
        pytest.skip('Build native host first')
    bridge = Bridge(tmp_path / 'MarketplaceTransferNew')
    environment = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    proc = subprocess.Popen([str(host)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    failure = queue.Queue()
    def chrome_peer():
        try:
            write_message(proc.stdin, {'event':'hello','extensionId':'test-pipe-peer','version':'test'})
            while True:
                message = read_message(proc.stdout)
                if message['command'] == 'heartbeat':
                    continue
                write_message(proc.stdin, {'id':message['id'],'result':{'echo':message['payload']}})
        except (EOFError, OSError, ValueError) as exc:
            failure.put(exc)
    threading.Thread(target=chrome_peer, daemon=True).start()
    try:
        deadline = time.monotonic()+15
        while not bridge.extension and time.monotonic() < deadline:
            time.sleep(.05)
        assert bridge.extension and bridge.connected
        assert bridge.call('test-echo', {'text':'Фото и описание'}, timeout=5) == {'echo':{'text':'Фото и описание'}}
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
        bridge.close()


def test_extension_identity_is_stable():
    root = Path(__file__).resolve().parents[1]
    assert bundled_extension_id(root) == 'napfoiejpmdfigpkolfmabacihhfmkbe'


def test_multiline_csv(tmp_path):
    from marketplace_transfer.importing import read_table, products_from_rows, guess
    path = tmp_path / 'multiline.csv'
    path.write_text('Название;Цена;Описание\nТовар;10;"Первая строка\nВторая строка"\n', encoding='utf-8')
    headers, rows = read_table(path)
    p = products_from_rows(headers, rows, guess(headers))[0]
    assert p.description == 'Первая строка\nВторая строка'
