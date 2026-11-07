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
    daemon_alive,
    daemon_endpoint,
    write_daemon_descriptor,
)
from context_rail.gateway.gateway import invoke
from helpers import (
    child_env,
    daemon_descriptor,
    daemon_request,
    holding_daemon_lock,
    serving_daemon,
)
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
        with holding_daemon_lock(store):
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


@pytest.mark.parametrize("owner", ["dead", "reused"])
def test_a_descriptor_no_daemon_holds_is_dropped_and_no_byte_reaches_the_port(
    store: Path, owner: str
) -> None:
    # A reused pid (here, this live test process) and a port some other program now
    # listens on look like a daemon from outside; only the lock tells them apart.
    pid = _dead_pid() if owner == "dead" else os.getpid()
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    probe.settimeout(0.3)
    try:
        write_daemon_descriptor(store, port=probe.getsockname()[1], pid=pid)
        assert _call(store) is None
        descriptor = daemon_endpoint(store)
        assert descriptor is not None and not descriptor.is_file()
        with pytest.raises(socket.timeout):
            probe.accept()
    finally:
        probe.close()


def test_a_daemon_is_alive_exactly_while_it_holds_the_lock(store: Path) -> None:
    assert daemon_alive(store) is False
    with holding_daemon_lock(store):
        assert daemon_alive(store) is True
    assert daemon_alive(store) is False


def test_a_live_daemon_is_found_by_its_lock_not_its_recorded_pid(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        held["pid"] = _dead_pid()
        descriptor.write_text(json.dumps(held), encoding="utf-8")
        served = _call(store)
    assert served is not None and served["ok"] is True


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
    with holding_daemon_lock(store):
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
    before = _find_count(store)
    with serving_daemon(store):
        served = _call(store, "add", params, read_only=False)
    assert served is not None
    assert _find_count(store) == before + (1 if served["ok"] else 0)
    direct = invoke("add", dict(params), store=store, actor="human")
    assert served["ok"] == direct["ok"]
    assert (served.get("error") or {}).get("code") == (direct.get("error") or {}).get(
        "code"
    )


def test_liveness_launches_no_subprocess_and_kills_nothing(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("liveness must not start or signal a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "kill", boom)
    assert daemon_alive(store) is False
    with holding_daemon_lock(store):
        assert daemon_alive(store) is True


def test_liveness_loads_no_foreign_function_library(store: Path) -> None:
    probe = (
        "import sys\n"
        "from context_rail.gateway.daemon import daemon_alive\n"
        "daemon_alive(sys.argv[1])\n"
        "print('ctypes' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(store)],
        capture_output=True,
        text=True,
        env=child_env(),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


@pytest.mark.parametrize("token", ["tökén", "", "x" * 5000])
def test_an_unusable_token_is_refused_not_an_internal_error(store: Path, token: str) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        refused = daemon_request(held["port"], {"tool": "status", "params": {}, "token": token})
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["reason"] == "daemon_token"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only mode bits")
def test_the_descriptor_holding_the_token_is_readable_only_by_its_owner(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        assert descriptor.stat().st_mode & 0o077 == 0


def test_a_request_past_its_deadline_is_declined_and_not_run(store: Path) -> None:
    before = _find_count(store)
    with serving_daemon(store):
        held = daemon_descriptor(store)
        assert held is not None
        declined = daemon_request(
            held["port"],
            {
                "tool": "add",
                "params": {"preset": "task", "title": "Too late"},
                "actor": "human",
                "actor_env": None,
                "token": held["token"],
                "deadline_monotonic": time.monotonic() - 5,
            },
        )
    assert declined["error"]["details"]["reason"] == "daemon_expired"
    assert _find_count(store) == before


def test_a_write_that_gave_up_waiting_never_runs_later(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_rail.gateway import gateway

    real = gateway.invoke

    def slow(tool: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if tool == "status":
            time.sleep(1.0)
        return real(tool, params, **kwargs)

    monkeypatch.setattr(gateway, "invoke", slow)
    monkeypatch.setenv("RAIL_DAEMON_TIMEOUT_MS", "300")
    before = _find_count(store)
    with serving_daemon(store):
        held = daemon_descriptor(store)
        assert held is not None
        busy = threading.Thread(
            target=daemon_request,
            args=(held["port"], {"tool": "status", "params": {}, "token": held["token"]}),
        )
        busy.start()
        time.sleep(0.2)
        result = _call(store, "add", {"preset": "task", "title": "Gave up"}, read_only=False)
        busy.join(10)
        time.sleep(0.5)
    assert result is not None and result["error"]["details"]["reason"] == "daemon_no_answer"
    assert _find_count(store) == before


def test_a_request_the_daemon_declined_to_start_is_a_miss(store: Path) -> None:
    declined = {
        "ok": False,
        "error": {
            "code": "PROCESS_ERROR",
            "message": "expired",
            "details": {"reason": "daemon_expired"},
        },
    }
    with _fake_daemon(store, reply=json.dumps(declined).encode("utf-8") + b"\n"):
        assert _call(store, "add", {"preset": "task", "title": "Nope"}, read_only=False) is None


def test_the_descriptor_is_not_the_one_an_older_client_reads(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        assert descriptor.name != "daemon.json"
        assert not (descriptor.parent / "daemon.json").exists()


@pytest.mark.parametrize(
    ("port", "pid"), [(99_999_999, None), (-1, None), (None, 2**40), (None, -7)]
)
def test_a_descriptor_with_impossible_numbers_is_a_miss(
    store: Path, port: int | None, pid: int | None
) -> None:
    write_daemon_descriptor(store, port=port or 5, pid=pid or os.getpid())
    with holding_daemon_lock(store):
        assert _call(store) is None


def test_the_stop_op_requires_the_token(store: Path) -> None:
    with serving_daemon(store):
        descriptor = daemon_endpoint(store)
        assert descriptor is not None
        held = json.loads(descriptor.read_text(encoding="utf-8"))
        refused = daemon_request(held["port"], {"op": "stop", "token": "wrong"})
        assert refused["error"]["code"] == "PERMISSION_DENIED"
        assert descriptor.is_file(), "a wrong token must leave the daemon running"
        assert daemon_request(held["port"], {"op": "stop", "token": held["token"]})["ok"] is True
        deadline = time.monotonic() + 5
        while descriptor.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not descriptor.is_file()


def _nested(depth: int) -> list[Any]:
    value: list[Any] = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("value", [object(), _nested(100_000)], ids=["unencodable", "too-deep"])
def test_any_failure_to_send_a_result_after_the_call_ran_is_unknown(
    store: Path, monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    from context_rail.gateway import gateway

    monkeypatch.setattr(gateway, "invoke", lambda *a, **k: {"ok": True, "value": value})
    with serving_daemon(store):
        result = _call(store, "add", {"preset": "task", "title": "Ran"}, read_only=False)
    assert result is not None and result["ok"] is False
    assert result["error"]["code"] == "PROCESS_ERROR"
    assert result["error"]["details"]["reason"] == "daemon_no_answer"


def test_every_served_call_reads_a_fresh_git_index(store: Path) -> None:
    from context_rail.store import git_index

    with serving_daemon(store):
        git_index._SNAPSHOT_CACHE[("planted", 0, 0)] = "stale"  # type: ignore[assignment]
        served = _call(store)
        assert served is not None and served["ok"] is True
        assert ("planted", 0, 0) not in git_index._SNAPSHOT_CACHE


def test_a_deadline_still_ahead_on_the_monotonic_clock_runs(store: Path) -> None:
    with serving_daemon(store):
        held = daemon_descriptor(store)
        assert held is not None
        served = daemon_request(
            held["port"],
            {
                "tool": "status",
                "params": {},
                "token": held["token"],
                "deadline_monotonic": time.monotonic() + 30,
            },
        )
    assert served["ok"] is True
