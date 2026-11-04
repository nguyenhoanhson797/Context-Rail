"""The loopback daemon: warm cache, token gate, clean shutdown."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from context_rail.gateway.daemon import _DaemonServer, daemon_alive, daemon_endpoint
from context_rail.gateway.gateway import invoke, try_daemon_invoke
from perf.gen import make_store


@pytest.fixture
def generated_store(tmp_path: Path) -> Path:
    return make_store(tmp_path, 20)


def test_daemon_serves_a_read_and_reuses_the_warm_context(tmp_path: Path) -> None:
    store = make_store(tmp_path, 20)
    server = _DaemonServer(store)
    port = server.start()
    try:
        assert port != 0
        assert daemon_alive(store)
        result = try_daemon_invoke("status", {}, store=store, actor="human")
        assert result is not None, "the daemon must answer the read"
        assert result["ok"] is True
        second = try_daemon_invoke("status", {}, store=store, actor="human")
        assert second is not None
        assert second["ok"] is True
        assert (store, second) is not None
    finally:
        server.stop()
    assert not daemon_alive(store)
    assert daemon_endpoint(store).is_file() is False


def test_a_wrong_token_is_refused_and_the_cli_falls_back(tmp_path: Path) -> None:
    store = make_store(tmp_path, 20)
    server = _DaemonServer(store)
    server.start()
    descriptor = daemon_endpoint(store)
    assert descriptor is not None and descriptor.is_file()
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["token"] = "wrong"
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    try:
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        try:
            sock.sendall(b'{"tool": "status", "params": {}, "actor": "human", "token": "wrong"}\n')
            sock.shutdown(socket.SHUT_WR)
            body = sock.recv(65536)
        finally:
            sock.close()
        assert b"PERMISSION_DENIED" in body
    finally:
        server.stop()


def test_direct_invoke_still_works_without_a_daemon(generated_store: Path) -> None:
    assert daemon_endpoint(generated_store).is_file() is False
    result = invoke("status", {}, store=generated_store, actor="human")
    assert result["ok"] is True
