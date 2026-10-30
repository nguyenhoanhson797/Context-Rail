"""The per-worktree store write lock.

The lock file lives in ``local_dir`` so each worktree of a repo and each store
outside git get their own. It is an OS file lock (``msvcrt.locking`` on Windows,
``flock`` elsewhere), so a killed holder releases it with its process, and it is
re-entrant within one process.
"""

from __future__ import annotations

import contextlib
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

from context_rail.store.location import local_dir

DEFAULT_LOCK_TIMEOUT = 30.0
LOCK_FILE = "write.lock"

if sys.platform == "win32":  # pragma: no cover - exercised per platform
    import msvcrt
else:  # pragma: no cover
    import fcntl


class StoreLockTimeout(Exception):
    """The store lock stayed busy for the whole timeout."""

    def __init__(self, path: Path, timeout: float):
        self.path = path
        self.timeout = timeout
        super().__init__(
            f"the store write lock is held by another process ({path}); "
            f"waited {timeout:g}s"
        )


class _Held:
    """In-process state of one lock path: a re-entrant guard plus the OS handle."""

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0
        self.fd: int | None = None


_held_guard = threading.Lock()
_held: dict[str, _Held] = {}


def _try_os_lock(fd: int) -> bool:
    """Take the OS lock once, without blocking. True when held."""
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            # LK_NBLCK in a poll loop of our own: LK_LOCK gives up after ~10s
            # with a confusing OSError.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _acquire_os_lock(path: Path, timeout: float) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if _try_os_lock(fd):
                return fd
            if time.monotonic() >= deadline:
                raise StoreLockTimeout(path, timeout)
            time.sleep(0.02 + random.random() * 0.03)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold an OS lock on ``path`` for the block, re-entrant in this process.

    The lock folder is created when missing. Raises ``StoreLockTimeout`` when
    another process keeps the lock past ``timeout``.
    """
    path = Path(path)
    key = str(path)
    with _held_guard:
        held = _held.setdefault(key, _Held())
    deadline = time.monotonic() + timeout
    if not held.rlock.acquire(timeout=max(timeout, 0.0)):
        raise StoreLockTimeout(path, timeout)
    try:
        if held.depth == 0:
            held.fd = _acquire_os_lock(path, max(deadline - time.monotonic(), 0.0))
        held.depth += 1
        try:
            yield
        finally:
            held.depth -= 1
            if held.depth == 0 and held.fd is not None:
                _os_unlock(held.fd)
                held.fd = None
    finally:
        held.rlock.release()


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold a non-blocking OS lock on ``path`` for the block, never re-entering.

    :func:`file_lock` is re-entrant so one process may nest its own writes; a
    single-instance lock must refuse a second holder even inside one process,
    so this always takes a fresh descriptor. Raises ``StoreLockTimeout`` when
    another holder has it.
    """
    release = take_exclusive(path)
    try:
        yield
    finally:
        release()


def take_exclusive(path: Path) -> Callable[[], None]:
    """Take the non-blocking lock now and return the function that releases it.

    A caller that must tell "this lock is busy" apart from a later failure wraps
    only this call: a ``StoreLockTimeout`` from the work inside the lock is a
    different problem and keeps its own error.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _acquire_os_lock(path, 0.0)
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        _os_unlock(fd)

    return release


@contextlib.contextmanager
def store_lock(store: Path, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold the store's write lock for the block, re-entrant in this process.

    The lock folder is created when missing, so bootstrap writes into a store
    that does not exist yet are serialised like any other write. Raises
    ``StoreLockTimeout`` when another process keeps the lock past ``timeout``.
    """
    with file_lock(local_dir(Path(store)) / LOCK_FILE, timeout):
        yield
