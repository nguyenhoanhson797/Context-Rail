"""The light client: a miss falls back, a write is never run twice."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.daemon import (
    call_daemon,
    daemon_endpoint,
    process_alive,
    write_daemon_descriptor,
)
from context_rail.gateway.gateway import invoke
from helpers import serving_daemon
from perf.gen import make_store

_NO_ANSWER_KEYED = "outcome unknown; repeating this exact command is safe, the key makes it idempotent"
_NO_ANSWER_UNKEYED = (
    "outcome unknown; do not repeat this command; re-read the item to see whether it applied"
)


@pytest.fixture
def store(tmp_path: Path, daemon_enabled: None) -> Path:
    return make_store(tmp_path, 5)


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


@contextmanager
def _fake_daemon(
    store: Path, *, reply: bytes | None, pid: int | None = None
) -> Iterator[SimpleNamespace]:
    """A listener that records one request and answers with ``reply``, or never."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    received = bytearray()
    held: list[socket.socket] = []

    def accept_one() -> None:
        listener.settimeout(10)
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        held.append(conn)
        conn.settimeout(10)
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                received.extend(chunk)
                if b"\n" in chunk:
                    break
            if reply is not None:
                conn.sendall(reply)
        except OSError:
            pass

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    write_daemon_descriptor(store, port=port, pid=pid)
    try:
        yield SimpleNamespace(port=port, received=received, listener=listener)
    finally:
        for conn in held:
            conn.close()
        listener.close()


def _call(store: Path, tool: str = "status", params: dict[str, Any] | None = None, *, read_only: bool = True) -> dict[str, Any] | None:
    return call_daemon(
        store,
        tool,
        dict(params or {}),
        actor="human",
        actor_env=None,
        read_only=read_only,
    )


def _find_count(store: Path) -> int:
    found = invoke("find", {"preset": "task", "limit": 100}, store=store, actor="human")
    return len(found["value"]["items"])


def test_a_dead_process_is_a_miss_and_no_byte_reaches_the_port(store: Path) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    probe.settimeout(0.3)
    try:
        write_daemon_descriptor(store, port=probe.getsockname()[1], pid=_dead_pid())
        assert _call(store) is None
        descriptor = daemon_endpoint(store)
        assert descriptor is not None and not descriptor.is_file()
        with pytest.raises(socket.timeout):
            probe.accept()
    finally:
        probe.close()


def test_a_version_mismatch_is_a_miss(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        held["version"] = "0.0.0"
        descriptor.write_text(json.dumps(held), encoding="utf-8")
        assert _call(store) is None
        deadline = time.monotonic() + 5
        while descriptor.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not descriptor.is_file(), "the old daemon should have stopped itself"


def test_connection_refused_is_a_miss(store: Path) -> None:
    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    write_daemon_descriptor(store, port=port)
    started = time.monotonic()
    assert _call(store) is None
    assert time.monotonic() - started < 1.0


def test_a_non_daemon_listener_is_a_miss(store: Path) -> None:
    with _fake_daemon(store, reply=b"hello\n"):
        assert _call(store) is None
    with _fake_daemon(store, reply=b"hello\n"):
        unknown = _call(store, "add", {"preset": "task", "title": "X"}, read_only=False)
    assert unknown is not None
    assert unknown["error"]["code"] == "PROCESS_ERROR"
    assert unknown["error"]["details"]["reason"] == "daemon_no_answer"


def test_a_read_with_no_answer_falls_back(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_DAEMON_TIMEOUT_MS", "200")
    with _fake_daemon(store, reply=None):
        started = time.monotonic()
        assert _call(store) is None
        assert time.monotonic() - started < 3.0


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"preset": "task", "title": "Late", "key": "k1"}, _NO_ANSWER_KEYED),
        ({"preset": "task", "title": "Late"}, _NO_ANSWER_UNKEYED),
    ],
)
def test_a_write_with_no_answer_is_unknown_and_never_rerun(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, Any],
    message: str,
) -> None:
    monkeypatch.setenv("RAIL_DAEMON_TIMEOUT_MS", "200")
    before = _find_count(store)
    with _fake_daemon(store, reply=None):
        result = _call(store, "add", params, read_only=False)
    assert result is not None and result["ok"] is False
    assert result["error"]["code"] == "PROCESS_ERROR"
    assert result["error"]["details"]["reason"] == "daemon_no_answer"
    assert result["error"]["message"] == message
    assert _find_count(store) == before


def test_a_token_the_daemon_refuses_is_a_miss(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        held["token"] = "wrong"
        descriptor.write_text(json.dumps(held), encoding="utf-8")
        assert _call(store) is None
        assert (
            _call(store, "add", {"preset": "task", "title": "Nope"}, read_only=False)
            is None
        )


def test_a_request_larger_than_one_read_matches_direct(store: Path) -> None:
    params = {"preset": "task", "title": "x" * 100_000}
    with serving_daemon(store):
        served = _call(store, "add", params, read_only=False)
    assert served is not None
    direct = invoke("add", dict(params), store=store, actor="human")
    assert served["ok"] == direct["ok"]
    assert (served.get("error") or {}).get("code") == (direct.get("error") or {}).get(
        "code"
    )


def test_liveness_launches_no_subprocess_and_kills_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = _dead_pid()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("liveness must not start a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    if os.name == "nt":
        monkeypatch.setattr(os, "kill", boom)
    assert process_alive(os.getpid()) is True
    assert process_alive(dead) is False


def _send(port: int, payload: dict[str, Any]) -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    return json.loads(data.decode("utf-8"))


def test_the_stop_op_requires_the_token(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        refused = _send(held["port"], {"op": "stop", "token": "wrong"})
        assert refused["error"]["code"] == "PERMISSION_DENIED"
        assert descriptor.is_file(), "a wrong token must leave the daemon running"
        assert _send(held["port"], {"op": "stop", "token": held["token"]})["ok"] is True
        deadline = time.monotonic() + 5
        while descriptor.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not descriptor.is_file()
