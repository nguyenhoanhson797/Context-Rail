"""The per-worktree store write lock."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from context_rail.store.location import local_dir
from context_rail.store.lock import StoreLockTimeout, store_lock

_SRC = Path(__file__).resolve().parents[2] / "src"

_HOLD_LOCK = """
import sys
import time

from context_rail.store.lock import store_lock

with store_lock(sys.argv[1]):
    print("ready", flush=True)
    time.sleep(2)
"""


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC)
    return env


def test_lock_is_reentrant_in_one_process(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    with store_lock(store):
        with store_lock(store):
            with store_lock(store, timeout=0.1):
                pass


def test_lock_bootstraps_a_missing_store(tmp_path: Path) -> None:
    store = tmp_path / "none" / "context-rail"
    with store_lock(store):
        assert (store / ".rail-local" / "write.lock").is_file()
    assert local_dir(store) / "write.lock" == store / ".rail-local" / "write.lock"
    assert (store / ".rail-local" / "write.lock").is_file()


def test_lock_file_lives_in_the_local_dir(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    with store_lock(store):
        assert (local_dir(store) / "write.lock").is_file()


def test_default_timeout_is_thirty_seconds() -> None:
    assert inspect.signature(store_lock).parameters["timeout"].default == 30.0


def test_lock_excludes_another_process(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(store)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )
    err = ""
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        started = time.monotonic()
        with pytest.raises(StoreLockTimeout):
            with store_lock(store, timeout=0.2):
                pass
        assert time.monotonic() - started < 1.5
    finally:
        _, err = child.communicate(timeout=60)
    assert child.returncode == 0, err
    with store_lock(store, timeout=0.2):
        pass


def test_lock_is_released_after_the_block(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    with store_lock(store):
        pass
    with store_lock(store, timeout=0.2):
        pass
