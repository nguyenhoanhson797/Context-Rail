"""A daemon starts on demand, stops on request or when idle, and never lingers."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail import cli
from context_rail.core.locate import local_dir
from context_rail.gateway.daemon import (
    _DaemonServer,
    call_daemon,
    daemon_alive,
    DaemonRunning,
    daemon_endpoint,
    spawn_daemon,
    stop_daemon,
    write_daemon_descriptor,
)
from helpers import (
    child_env,
    daemon_descriptor,
    process_alive,
    run_cli,
    run_cli_probe,
    serving_daemon,
)
from perf.gen import make_store


def _live_pid(store: Path) -> int | None:
    held = daemon_descriptor(store)
    pid = held.get("pid") if held else None
    return pid if isinstance(pid, int) and process_alive(pid) else None


def _wait_for(predicate: Callable[[], Any], seconds: float, what: str) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out after {seconds}s waiting for {what}")


def _kill(pid: int) -> None:
    if process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _daemon_processes(store: Path) -> list[int]:
    """Top-level ``serve daemon`` processes for ``store`` (a launcher and its child count once)."""
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process | ForEach-Object "
            "{ '{0}|{1}|{2}' -f $_.ProcessId, $_.ParentProcessId, $_.CommandLine }"
        )
        command = ["powershell", "-NoProfile", "-Command", script]
        split = "|"
    else:
        command = ["ps", "-eo", "pid=,ppid=,args="]
        split = None
    listing = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=60
    ).stdout
    rows: dict[int, tuple[int, str]] = {}
    for line in listing.splitlines():
        parts = line.split(split, 2) if split else line.split(None, 2)
        if len(parts) == 3 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    ours = {
        pid
        for pid, (_, args) in rows.items()
        if "serve daemon" in args and str(store) in args
    }
    return sorted(pid for pid in ours if rows[pid][0] not in ours)


class _Daemons:
    """Real daemons a test started, killed at teardown so none outlives it."""

    def __init__(self) -> None:
        self.stores: list[Path] = []
        self.pids: set[int] = set()

    def start(self, store: Path) -> int:
        self.stores.append(store)
        proc = run_cli(store.parent, "next", store=store, env={"RAIL_NO_DAEMON": None})
        assert proc.returncode == 0, proc.stderr
        return self.wait_live(store)

    def wait_live(self, store: Path, *, other_than: int | None = None) -> int:
        if store not in self.stores:
            self.stores.append(store)
        def replacement() -> int | None:
            found = _live_pid(store)
            return found if found != other_than else None

        pid = _wait_for(replacement, 10, "a live daemon")
        self.pids.add(pid)
        return pid

    def close(self) -> None:
        for store in self.stores:
            try:
                found = _live_pid(store)
            except (AssertionError, OSError):
                found = None
            if found:
                self.pids.add(found)
        for pid in self.pids:
            _kill(pid)


@pytest.fixture
def daemons() -> Iterator[_Daemons]:
    held = _Daemons()
    try:
        yield held
    finally:
        held.close()


@pytest.fixture
def store(tmp_path: Path, daemon_enabled: None) -> Path:
    return make_store(tmp_path, 5)


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake(args: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((list(args), kwargs))
        return SimpleNamespace(pid=0)

    monkeypatch.setattr(subprocess, "Popen", fake)
    return calls


def test_the_first_call_starts_a_daemon_and_a_later_call_is_served_by_it(
    store: Path, daemons: _Daemons
) -> None:
    daemons.start(store)
    proc, loaded = run_cli_probe(store.parent, "next", store=store)
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is False


def test_an_idle_daemon_holds_nothing_inside_the_project(
    tmp_path: Path, daemon_enabled: None, daemons: _Daemons
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = make_store(project, 5)
    daemons.start(store)
    os.rename(project, tmp_path / "renamed")


def test_concurrent_first_calls_start_one_daemon(
    store: Path, popen_calls: list[Any]
) -> None:
    results: list[bool] = []
    gate = threading.Barrier(8)

    def race() -> None:
        gate.wait()
        results.append(spawn_daemon(store))

    threads = [threading.Thread(target=race) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(results) == [False] * 7 + [True]
    assert len(popen_calls) == 1


def test_concurrent_first_calls_from_separate_processes_start_one_daemon(
    store: Path, daemons: _Daemons
) -> None:
    daemons.stores.append(store)
    done: list[subprocess.CompletedProcess[str]] = []

    def call() -> None:
        done.append(run_cli(store.parent, "next", store=store, env={"RAIL_NO_DAEMON": None}))

    threads = [threading.Thread(target=call) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert [proc.returncode for proc in done] == [0, 0, 0, 0]
    daemons.wait_live(store)
    started = _daemon_processes(store)
    daemons.pids.update(started)
    assert len(started) == 1, started


def test_a_stale_spawn_lock_is_replaced(store: Path, popen_calls: list[Any]) -> None:
    lock = local_dir(store) / "daemon.spawn"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    old = time.time() - 60
    os.utime(lock, (old, old))
    assert spawn_daemon(store) is True
    assert len(popen_calls) == 1


def test_a_fresh_spawn_lock_blocks_a_second_spawn(store: Path, popen_calls: list[Any]) -> None:
    lock = local_dir(store) / "daemon.spawn"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    assert spawn_daemon(store) is False
    assert popen_calls == []


def test_spawn_arguments(store: Path, popen_calls: list[Any]) -> None:
    assert spawn_daemon(store) is True
    (command, options), = popen_calls
    assert command == [
        sys.executable, "-P", "-m", "context_rail", "--store", str(store), "serve", "daemon",
    ]
    assert options["cwd"] == tempfile.gettempdir()
    for stream in ("stdin", "stdout", "stderr"):
        assert options[stream] == subprocess.DEVNULL
    if os.name == "nt":
        assert options["creationflags"] == 0x8 | 0x200
    else:
        assert options["start_new_session"] is True


def test_the_daemon_never_imports_a_package_planted_in_its_directory(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_popen = subprocess.Popen
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda args, **_: spawned.append(list(args)) or SimpleNamespace()
    )
    assert spawn_daemon(store) is True
    monkeypatch.setattr(subprocess, "Popen", real_popen)
    command, = spawned
    planted = tmp_path / "shared-tmp"
    (planted / "context_rail").mkdir(parents=True)
    for name in ("__init__.py", "__main__.py"):
        (planted / "context_rail" / name).write_text(
            "raise SystemExit('planted')\n", encoding="utf-8"
        )
    launcher = command[: command.index("context_rail") + 1]
    proc = subprocess.run(
        [*launcher, "--help"],
        cwd=planted,
        capture_output=True,
        text=True,
        env=child_env(),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "planted" not in proc.stdout + proc.stderr


def test_a_second_daemon_for_the_same_store_refuses_to_start(store: Path) -> None:
    with serving_daemon(store):
        before = daemon_descriptor(store)
        with pytest.raises(DaemonRunning):
            _DaemonServer(store).start()
        assert daemon_descriptor(store) == before
        answer = call_daemon(store, "status", {}, actor="human", actor_env=None, read_only=True)
        assert answer is not None and answer["ok"] is True


def test_serving_a_store_that_already_has_a_daemon_is_refused(store: Path) -> None:
    from context_rail.gateway.gateway import invoke

    with serving_daemon(store):
        refused = invoke("serve daemon", {"port": 0}, store=store, actor="human")
    assert refused["ok"] is False
    assert refused["error"]["code"] == "CONFLICT"
    assert refused["error"]["details"]["reason"] == "daemon_running"

def test_the_daemon_exits_when_idle(store: Path) -> None:
    server = _DaemonServer(store, idle_s=1)
    thread = threading.Thread(target=server.start_blocking, daemon=True)
    thread.start()
    try:
        _wait_for(lambda: daemon_alive(store), 5, "the daemon to start")
        _wait_for(lambda: not daemon_alive(store), 4, "the idle daemon to exit")
        assert not daemon_endpoint(store).exists()
        thread.join(5)
        assert not thread.is_alive()
    finally:
        server.stop()


def test_a_request_keeps_an_idle_daemon_alive(store: Path) -> None:
    server = _DaemonServer(store, idle_s=1)
    thread = threading.Thread(target=server.start_blocking, daemon=True)
    thread.start()
    try:
        _wait_for(lambda: daemon_alive(store), 5, "the daemon to start")
        for _ in range(6):
            time.sleep(0.4)
            answer = call_daemon(
                store, "status", {}, actor="human", actor_env=None, read_only=True
            )
            assert answer is not None and answer["ok"] is True
        assert daemon_alive(store)
        _wait_for(lambda: not daemon_alive(store), 4, "the idle daemon to exit")
    finally:
        server.stop()


def test_the_idle_time_comes_from_the_environment(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_DAEMON_IDLE_S", "1")
    server = _DaemonServer(store)
    thread = threading.Thread(target=server.start_blocking, daemon=True)
    thread.start()
    try:
        _wait_for(lambda: daemon_alive(store), 5, "the daemon to start")
        _wait_for(lambda: not daemon_alive(store), 4, "the idle daemon to exit")
    finally:
        server.stop()


@pytest.mark.parametrize("raw", ["0", "-5", "soon", ""])
def test_an_unusable_idle_time_falls_back_to_ten_minutes(
    store: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("RAIL_DAEMON_IDLE_S", raw)
    assert _DaemonServer(store)._idle_s == 600


def test_stop_ends_the_daemon(store: Path, daemons: _Daemons) -> None:
    pid = daemons.start(store)
    proc = run_cli(store.parent, "serve", "daemon", "--stop", store=store)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["value"]["stopped"] is True
    _wait_for(lambda: not process_alive(pid), 5, "the daemon to exit")
    assert not daemon_endpoint(store).exists()


def test_stop_with_no_daemon_reports_nothing_stopped(store: Path) -> None:
    proc = run_cli(store.parent, "serve", "daemon", "--stop", store=store)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["value"]["stopped"] is False


def test_a_hard_killed_daemon_is_replaced_on_the_next_call(
    store: Path, daemons: _Daemons
) -> None:
    first = daemons.start(store)
    os.kill(first, signal.SIGTERM)
    _wait_for(lambda: not process_alive(first), 5, "the daemon to die")
    assert daemon_descriptor(store) is not None
    proc = run_cli(store.parent, "next", store=store, env={"RAIL_NO_DAEMON": None})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    second = daemons.wait_live(store, other_than=first)
    assert second != first


def test_a_miss_starts_one_daemon_and_does_not_wait(
    store: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--store", str(store), "next"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    (command, _), = popen_calls
    assert command[-2:] == ["serve", "daemon"]


def test_a_served_call_starts_no_second_daemon(
    store: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    with serving_daemon(store):
        assert cli.main(["--store", str(store), "next"]) == 0
    assert popen_calls == []


@pytest.mark.parametrize("args", [["doctor"], ["serve", "daemon", "--stop"]])
def test_an_ineligible_call_never_starts_a_daemon(
    store: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    assert cli.main(["--store", str(store), *args]) == 0
    assert popen_calls == []


def test_the_suite_switch_prevents_spawning(
    tmp_path: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    store = make_store(tmp_path, 5)
    assert os.environ.get("RAIL_NO_DAEMON")
    assert cli.main(["--store", str(store), "next"]) == 0
    assert popen_calls == []
    assert not daemon_endpoint(store).exists()


def test_the_daemon_does_not_run_as_the_actor_that_started_it(
    store: Path, popen_calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "implementer")
    monkeypatch.setenv("RAIL_KEEP_ME", "yes")
    assert spawn_daemon(store) is True
    (_, options), = popen_calls
    assert "RAIL_ACTOR" not in options["env"]
    assert options["env"]["RAIL_KEEP_ME"] == "yes"


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_reused_process_id_does_not_keep_a_new_daemon_from_starting(
    store: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    write_daemon_descriptor(store, port=_closed_port(), pid=os.getpid())
    assert daemon_alive(store) is False
    assert cli.main(["--store", str(store), "next"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    (command, _), = popen_calls
    assert command[-2:] == ["serve", "daemon"]
    assert not daemon_endpoint(store).exists()


def test_a_busy_daemon_with_a_burst_of_waiting_callers_stays_the_daemon(
    store: Path, monkeypatch: pytest.MonkeyPatch, popen_calls: list[Any]
) -> None:
    from context_rail.gateway import gateway

    real = gateway.invoke

    def slow(tool: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if tool == "status":
            time.sleep(6.0)
        return real(tool, params, **kwargs)

    monkeypatch.setattr(gateway, "invoke", slow)
    waiting: list[socket.socket] = []
    refused: list[OSError] = []

    def wait_in_line(port: int) -> None:
        try:
            waiting.append(socket.create_connection(("127.0.0.1", port), timeout=4))
        except OSError as error:
            refused.append(error)

    with serving_daemon(store):
        held = daemon_descriptor(store)
        assert held is not None
        busy = socket.create_connection(("127.0.0.1", held["port"]), timeout=10)
        busy.sendall(
            json.dumps({"tool": "status", "params": {}, "token": held["token"]}).encode() + b"\n"
        )
        busy.shutdown(socket.SHUT_WR)
        time.sleep(0.3)
        callers = [
            threading.Thread(target=wait_in_line, args=(held["port"],)) for _ in range(40)
        ]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(10)
        try:
            assert refused == []
            assert daemon_alive(store) is True
            assert daemon_descriptor(store) == held
            assert spawn_daemon(store) is False
            assert popen_calls == []
        finally:
            for conn in waiting:
                conn.close()
            busy.close()


def test_a_broken_git_marker_is_not_a_crash_on_the_fast_path(    store: Path, popen_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    (store.parent / ".git").write_text("gitdir: ../missing\n", encoding="utf-8")
    code = cli.main(["--store", str(store), "next"])
    fast = capsys.readouterr().out
    direct_code = cli.main(["--no-daemon", "--store", str(store), "next"])
    direct = capsys.readouterr().out
    assert (code, fast) == (direct_code, direct)
    assert popen_calls == []


def test_stop_daemon_clears_a_descriptor_whose_process_is_gone(store: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    from context_rail.gateway.daemon import write_daemon_descriptor

    write_daemon_descriptor(store, port=1, pid=child.pid)
    assert stop_daemon(store) is False
    assert not daemon_endpoint(store).exists()
