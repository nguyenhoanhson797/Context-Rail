"""The single-instance CTO lock: one tick at a time, released on every exit."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.runner import dispatch as local_dispatch
from context_rail.store.location import local_dir
from helpers import process_alive, run_cli, start_cli


def tick(store: Path, key: str, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"key": key}
    args.update(extra)
    return invoke("cto tick", args, store=store, actor="cto")


def wait_for(predicate: Any, *, timeout: float = 90.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("the expected state never appeared")


def kill_tree(proc: Any) -> None:
    import os
    import subprocess

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.communicate(timeout=30)
    except Exception:  # pragma: no cover - defensive
        proc.kill()


@pytest.mark.timeout(120)
def test_second_tick_conflicts(leaf_item, fake_droid, tick_holder) -> None:
    with tick_holder(leaf_item.store) as lock_path:
        r = tick(leaf_item.store, "t5")

        assert r["ok"] is False, r
        assert r["error"]["code"] == "CONFLICT"
        assert r["error"]["details"]["holder"]
        assert r["error"]["details"]["lock"] == str(lock_path)
        assert fake_droid.log() == []


@pytest.mark.timeout(180)
def test_a_running_tick_holds_the_lock_and_a_following_one_waits_its_turn(
    leaf_item, fake_droid, droid_env
) -> None:
    fake_droid.set_behaviors(["timeout", "success"])
    first = start_cli(
        leaf_item.root, "cto", "tick", "--key", "t6a", "--timeout", "120", store=leaf_item.store
    )
    try:
        wait_for(lambda: bool(fake_droid.log()))
        second = run_cli(
            leaf_item.root, "cto", "tick", "--key", "t6b", "--timeout", "60", store=leaf_item.store
        )
        payload = json.loads(second.stdout)
        assert second.returncode == 1, second.stderr
        assert payload["error"]["code"] == "CONFLICT"
        assert payload["error"]["details"]["holder"]["tick_id"]
        assert len(fake_droid.log()) == 1
    finally:
        kill_tree(first)

    child = fake_droid.root / "fake-droid-child.json"
    if child.is_file():
        pid = json.loads(child.read_text(encoding="utf-8"))["pid"]
        wait_for(lambda: not process_alive(pid))

    third = tick(leaf_item.store, "t6c")
    assert is_ok(third), third
    assert third["value"]["status"] == "completed"
    assert len(fake_droid.log()) == 2


@pytest.mark.timeout(120)
def test_the_lock_is_released_when_the_child_times_out(
    leaf_item, fake_droid, droid_env, tick_holder
) -> None:
    fake_droid.set_behaviors(["timeout", "success"])
    failed = tick(leaf_item.store, "t7a", timeout=5)
    assert is_ok(failed), failed
    assert failed["value"]["status"] == "failed"

    with tick_holder(leaf_item.store):
        pass

    fake_droid.set_behaviors(["success"])
    assert is_ok(tick(leaf_item.store, "t7b"))


def test_the_lock_is_released_when_the_launch_faults(
    leaf_item, fake_droid, monkeypatch: pytest.MonkeyPatch, tick_holder
) -> None:
    from context_rail.gateway.handlers import cto as cto_handler

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the launcher exploded")

    original = cto_handler.run_droid
    monkeypatch.setattr(cto_handler, "run_droid", explode)
    try:
        r = tick(leaf_item.store, "t8a")
    finally:
        monkeypatch.setattr(cto_handler, "run_droid", original)

    assert r["ok"] is False, r

    with tick_holder(leaf_item.store):
        pass
    assert is_ok(tick(leaf_item.store, "t8b"))


def test_the_lock_is_independent_of_the_store_write_lock(
    leaf_item, fake_droid, droid_env
) -> None:
    from context_rail.store.lock import store_lock

    with store_lock(leaf_item.store):
        r = tick(leaf_item.store, "t9")

    assert is_ok(r), r
    assert r["value"]["status"] == "completed"


def test_a_conflicted_tick_writes_no_records(leaf_item, fake_droid, tick_holder) -> None:
    from helpers import records_of

    before = len(records_of(leaf_item.store, "run.completed"))
    with tick_holder(leaf_item.store):
        r = tick(leaf_item.store, "t10")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "CONFLICT"
    assert len(records_of(leaf_item.store, "run.completed")) == before
    assert records_of(leaf_item.store, "tick.started") == []


def test_the_lock_file_lives_under_local_dir(leaf_item, fake_droid, droid_env) -> None:
    assert is_ok(tick(leaf_item.store, "t11"))

    path = local_dispatch.tick_lock_path(leaf_item.store)
    assert path == local_dir(leaf_item.store) / "cto.lock"
    assert path.is_file()
    assert not path.is_relative_to(leaf_item.store)


def test_an_unrelated_lock_timeout_is_not_rewritten_as_a_tick_conflict(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store-lock timeout keeps its own error and still runs the abort cleanup."""
    from context_rail.gateway.handlers import cto as cto_handler
    from context_rail.store.lock import StoreLockTimeout
    from helpers import records_of

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise StoreLockTimeout(local_dir(leaf_item.store) / "write.lock", 30.0)

    original = cto_handler.commit
    monkeypatch.setattr(cto_handler, "commit", refuse)
    before = len(records_of(leaf_item.store, "tick.started"))
    try:
        r = tick(leaf_item.store, "t12")
    finally:
        monkeypatch.setattr(cto_handler, "commit", original)

    assert r["ok"] is False, r
    assert r["error"]["code"] != "CONFLICT", r
    assert "single-instance" not in r["error"]["message"]
    assert len(records_of(leaf_item.store, "tick.started")) == before
    assert fake_droid.log() == []
    assert local_dispatch.read_tick_binding(leaf_item.store, "t12") is None
    folder = local_dir(leaf_item.store) / "tick"
    assert [path for path in folder.glob("*.json")] == []

    # The aborted tick released the lock, so a fresh key can still take it.
    assert is_ok(tick(leaf_item.store, "t12b"))
