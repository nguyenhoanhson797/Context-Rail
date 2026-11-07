"""Local dispatch state: worktrees, branches, locks and progress.

Dispatch truth is a pair of immutable Records; this module only keeps the
control files that make a launch survivable: where its worktree lives, which
phase it reached, and the key it was started with. Everything here is written
under ``local_dir`` (or the OS temporary directory for the checkout itself), so
nothing lands in the repository tree, its git dir or the store.

The CTO tick keeps the same kind of state: one OS lock in ``local_dir/cto.lock``
that makes a second concurrent tick impossible, plus the key binding and the
progress file a replay reads after a crash.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.core.util import sha256_hex
from context_rail.runner.control import (
    binding_payload,
    merge_control,
    read_control,
    write_control,
)
from context_rail.store.location import local_dir, worktree_git_dir
from context_rail.model.primitives import WINDOWS_RESERVED_NAMES
from context_rail.store.lock import StoreLockTimeout, file_lock, take_exclusive
from context_rail.store.write import WriteRefused

__all__ = [
    "DispatchPaths",
    "PROGRESS_STATES",
    "acquire_dispatch_lock",
    "acquire_tick_lock",
    "active_dispatches",
    "dispatch_paths",
    "key_binding_path",
    "pending_dispatches",
    "progress_path",
    "project_root",
    "read_binding",
    "read_progress",
    "read_review_binding",
    "read_review_progress",
    "read_session",
    "read_tick_binding",
    "read_tick_progress",
    "register_review_worktree",
    "register_worktree",
    "remove_binding",
    "remove_review_binding",
    "remove_tick_binding",
    "review_binding_path",
    "review_paths",
    "review_progress_path",
    "session_path",
    "tick_binding_path",
    "tick_lock_path",
    "tick_progress_path",
    "write_binding",
    "write_progress",
    "write_review_binding",
    "write_review_progress",
    "write_session",
    "write_tick_binding",
    "write_tick_progress",
    "worktree_root",
]

DISPATCH_DIR = "dispatch"
REVIEW_DIR = "review"
TICK_DIR = "tick"
TICK_LOCK = "cto.lock"
KEY_DIR = "keys"
LOCK_DIR = "locks"
WORKTREE_PARENT = "context-rail-worktrees"
PROGRESS_STATES = ("started", "worktree", "running", "verifying", "completed")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_BRANCH = re.compile(r"^rail/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_REVIEW_BRANCH = re.compile(
    r"^rail-review/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})$"
)


@dataclass(frozen=True)
class DispatchPaths:
    """Where one dispatch's private checkout and branch live."""

    root: Path
    worktree: Path
    branch: str


def project_root(store: Path) -> Path:
    """The Git worktree that holds the canonical store."""
    located = worktree_git_dir(Path(store))
    if located is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "the canonical store is not inside a Git worktree",
        )
    return located[1]


def worktree_root(store: Path) -> Path:
    """``<temp>/context-rail-worktrees/<project-location-hash>``.

    The hash is derived from the canonical project path, so the same project
    keeps one root across processes while two projects never share one. Tests
    point ``RAIL_WORKTREE_ROOT`` at a per-test folder so concurrent sessions
    can never share or delete each other's checkouts.
    """
    root = project_root(store).resolve()
    location = os.path.normcase(str(root))
    digest = sha256_hex(location.encode("utf-8"))[:12]
    parent = os.environ.get("RAIL_WORKTREE_ROOT")
    base = Path(parent) if parent else Path(tempfile.gettempdir()) / WORKTREE_PARENT
    return base / digest


def dispatch_paths(store: Path, item: str, dispatch_id: str) -> DispatchPaths:
    """The validated worktree path and branch name for one dispatch."""
    slug = _slug(item)
    ident = _dispatch_id(dispatch_id)
    root = worktree_root(store)
    return DispatchPaths(
        root=root, worktree=root / ident, branch=f"rail/{slug}/{ident}"
    )


def progress_path(store: Path, dispatch_id: str) -> Path:
    """The local control file of one dispatch."""
    return local_dir(Path(store)) / DISPATCH_DIR / f"{_dispatch_id(dispatch_id)}.json"


def session_path(store: Path, item: str) -> Path:
    """The local note that remembers an Item's last agent session."""
    return local_dir(Path(store)) / "sessions" / f"{_slug(item)}.json"


def read_session(store: Path, item: str, runner: str) -> str | None:
    """The previous session id for this Item and runner, or None.

    A session belongs to the CLI that created it, so a session recorded under
    another runner is ignored rather than continued into a different tool.
    """
    from context_rail.runner.control import read_control

    payload = read_control(
        session_path(store, item), identity=_slug(item), key="item"
    )
    if not payload or payload.get("corrupt"):
        return None
    if payload.get("runner") != runner:
        return None
    session = payload.get("session")
    if not isinstance(session, str) or not _SESSION_ID.match(session):
        return None
    return session


def write_session(store: Path, item: str, runner: str, session: str | None) -> None:
    """Remember the session a launch reported, for the next dispatch."""
    from context_rail.runner.control import merge_control

    if not session:
        return
    merge_control(
        session_path(store, item),
        identity=_slug(item),
        key="item",
        values={"runner": runner, "session": session},
    )


def review_paths(store: Path, item: str, review_id: str) -> DispatchPaths:
    """The validated worktree path and branch name for one review."""
    slug = _slug(item)
    ident = _dispatch_id(review_id)
    root = worktree_root(store)
    return DispatchPaths(
        root=root,
        worktree=root / ident,
        branch=f"rail-review/{slug}/{ident}",
    )


def review_progress_path(store: Path, review_id: str) -> Path:
    """The local control file of one review."""
    return local_dir(Path(store)) / REVIEW_DIR / f"{_dispatch_id(review_id)}.json"


def review_binding_path(store: Path, key: str) -> Path:
    """The local file binding one idempotency key to its review id."""
    if not isinstance(key, str) or not key.strip():
        raise WriteRefused("VALIDATION_FAILED", "a review key must be text")
    digest = sha256_hex(key.encode("utf-8"))
    return local_dir(Path(store)) / REVIEW_DIR / KEY_DIR / f"{digest}.json"


def key_binding_path(store: Path, key: str) -> Path:
    """The local file binding one idempotency key to its dispatch id."""
    if not isinstance(key, str) or not key.strip():
        raise WriteRefused("VALIDATION_FAILED", "a dispatch key must be text")
    digest = sha256_hex(key.encode("utf-8"))
    return local_dir(Path(store)) / DISPATCH_DIR / KEY_DIR / f"{digest}.json"


@contextlib.contextmanager
def acquire_dispatch_lock(store: Path, item: str, dispatch_id: str) -> Iterator[None]:
    """Hold the per-item launch lock; a second live launch is refused."""
    slug = _slug(item)
    _dispatch_id(dispatch_id)
    path = local_dir(Path(store)) / DISPATCH_DIR / LOCK_DIR / f"{slug}.lock"
    try:
        with file_lock(path, timeout=0):
            yield
    except StoreLockTimeout:
        raise WriteRefused(
            "CONFLICT",
            f"another dispatch for {slug!r} is already running",
            {"item": slug, "lock": str(path)},
        ) from None


def read_progress(store: Path, dispatch_id: str) -> dict[str, Any] | None:
    """The stored progress of one dispatch, or None when there is none."""
    ident = _dispatch_id(dispatch_id)
    return read_control(
        progress_path(store, ident), identity=ident, key="dispatch_id"
    )


def tick_lock_path(store: Path) -> Path:
    """The single-instance CTO lock file, beside the write lock's folder."""
    return local_dir(Path(store)) / TICK_LOCK


@contextlib.contextmanager
def acquire_tick_lock(store: Path) -> Iterator[Path]:
    """Hold the CTO lock for the whole child; a live holder is a ``CONFLICT``.

    The lock is non-blocking and unrelated to ``write.lock``: a tick keeps it
    while the CTO runs ``rail`` commands that take the write lock themselves.
    A holder's own progress file names who holds it.
    """
    path = tick_lock_path(store)
    try:
        release = take_exclusive(path)
    except StoreLockTimeout:
        raise WriteRefused(
            "CONFLICT",
            "another CTO tick holds the single-instance lock",
            {"lock": str(path), "holder": _tick_holder(store)},
        ) from None
    try:
        yield path
    finally:
        release()


def _tick_holder(store: Path) -> dict[str, Any]:
    """The best available description of the tick holding the lock.

    The lock file itself carries no payload, so the holder is the newest
    unfinished progress file; a corrupt one still blocks, and an unknown
    holder is reported as such rather than invented.
    """
    folder = local_dir(Path(store)) / TICK_DIR
    candidates: list[dict[str, Any]] = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            payload = read_control(path, identity=path.stem, key="tick_id")
            if payload is None:
                continue
            if payload.get("state") in ("completed", "failed"):
                continue
            candidates.append(payload)
    if not candidates:
        return {"tick_id": None, "phase": "unknown"}
    newest = max(candidates, key=lambda entry: str(entry.get("started_at") or ""))
    return {
        "tick_id": newest.get("tick_id"),
        "phase": newest.get("phase", "unknown"),
        "key": newest.get("key"),
        "started_at": newest.get("started_at"),
        "corrupt": bool(newest.get("corrupt")),
    }


def tick_progress_path(store: Path, tick_id: str) -> Path:
    """The local control file of one tick."""
    return local_dir(Path(store)) / TICK_DIR / f"{_dispatch_id(tick_id)}.json"


def read_tick_progress(store: Path, tick_id: str) -> dict[str, Any] | None:
    """The stored progress of one tick, or None when there is none."""
    ident = _dispatch_id(tick_id)
    return read_control(
        tick_progress_path(store, ident), identity=ident, key="tick_id"
    )


def write_tick_progress(store: Path, tick_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into one tick's progress file and return it."""
    ident = _dispatch_id(tick_id)
    return merge_control(
        tick_progress_path(store, ident),
        identity=ident,
        key="tick_id",
        values=values,
    )


def tick_binding_path(store: Path, key: str) -> Path:
    """The local file binding one idempotency key to its tick."""
    if not isinstance(key, str) or not key.strip():
        raise WriteRefused("VALIDATION_FAILED", "a tick key must be text")
    digest = sha256_hex(key.encode("utf-8"))
    return local_dir(Path(store)) / TICK_DIR / KEY_DIR / f"{digest}.json"


def read_tick_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The tick one key already names, or None."""
    return read_control(tick_binding_path(store, key), identity=key, key="key")


def write_tick_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Persist the key-to-tick binding before the child is launched."""
    return write_control(tick_binding_path(store, key), binding_payload(key, values))


def remove_tick_binding(store: Path, key: str) -> None:
    """Forget a tick binding whose launch never reached the droid."""
    tick_binding_path(store, key).unlink(missing_ok=True)


def write_progress(store: Path, dispatch_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into one dispatch's progress file and return it."""
    ident = _dispatch_id(dispatch_id)
    return merge_control(
        progress_path(store, ident),
        identity=ident,
        key="dispatch_id",
        values=values,
    )


def read_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The dispatch id one key already names, or None."""
    return read_control(key_binding_path(store, key), identity=key, key="key")


def read_review_progress(store: Path, review_id: str) -> dict[str, Any] | None:
    """The stored progress of one review, or None when there is none."""
    ident = _dispatch_id(review_id)
    return read_control(
        review_progress_path(store, ident), identity=ident, key="dispatch_id"
    )


def write_review_progress(store: Path, review_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into one review's progress file and return it."""
    ident = _dispatch_id(review_id)
    return merge_control(
        review_progress_path(store, ident),
        identity=ident,
        key="dispatch_id",
        values=values,
    )


def read_review_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The review id one key already names, or None."""
    return read_control(review_binding_path(store, key), identity=key, key="key")


def write_review_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Persist the key-to-review binding before any branch is created."""
    return write_control(review_binding_path(store, key), binding_payload(key, values))


def remove_review_binding(store: Path, key: str) -> None:
    """Forget a review binding whose launch never started."""
    review_binding_path(store, key).unlink(missing_ok=True)


def write_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Persist the key-to-dispatch binding before any branch is created."""
    return write_control(key_binding_path(store, key), binding_payload(key, values))


def remove_binding(store: Path, key: str) -> None:
    """Forget a binding whose launch never started."""
    key_binding_path(store, key).unlink(missing_ok=True)


def pending_dispatches(store: Path, item: str) -> list[dict[str, Any]]:
    """Every unfinished dispatch that may still own this item's checkout.

    A progress file that cannot be read blocks too: an unknown live phase is
    never a licence to launch a second uncontrolled run.
    """
    folder = local_dir(Path(store)) / DISPATCH_DIR
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        payload = read_control(path, identity=path.stem, key="dispatch_id")
        if payload is None:
            continue
        if payload.get("corrupt"):
            payload.setdefault("phase", "unknown")
            found.append(payload)
            continue
        if payload.get("state") == "completed":
            continue
        if payload.get("item") not in (None, item):
            continue
        found.append(payload)
    return found


def active_dispatches(store: Path) -> list[dict[str, Any]]:
    """Every readable unfinished dispatch with the commit and code area it holds."""
    folder = local_dir(Path(store)) / DISPATCH_DIR
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        payload = read_control(path, identity=path.stem, key="dispatch_id")
        if payload is None or payload.get("corrupt") or payload.get("state") == "completed":
            continue
        area = payload.get("code_area")
        found.append(
            {
                "dispatch_id": path.stem,
                "item": payload.get("item"),
                "base_commit": payload.get("base_commit"),
                "code_area": [entry for entry in area if isinstance(entry, str)]
                if isinstance(area, list)
                else [],
            }
        )
    return found


def register_worktree(store: Path, dispatch_id: str, path: Path, branch: str) -> None:
    """Record the checkout one dispatch created, before it is used."""
    ident = _dispatch_id(dispatch_id)
    root = worktree_root(store)
    candidate = Path(path)
    if candidate.name != ident or not _same_path(candidate.parent, root):
        raise WriteRefused(
            "CONFLICT",
            "the dispatch worktree is not this project's dispatch directory",
            {"dispatch_id": ident, "path": str(candidate), "root": str(root)},
        )
    match = _BRANCH.match(branch) if isinstance(branch, str) else None
    if match is None or match.group(2) != ident:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{branch!r} is not a dispatch branch for {ident!r}",
            {"dispatch_id": ident, "branch": branch},
        )
    write_progress(
        store,
        ident,
        state="worktree",
        phase="worktree",
        worktree=str(candidate.resolve()),
        branch=branch,
    )


def register_review_worktree(store: Path, review_id: str, path: Path, branch: str) -> None:
    """Record the checkout one review created, before it is used."""
    ident = _dispatch_id(review_id)
    root = worktree_root(store)
    candidate = Path(path)
    if candidate.name != ident or not _same_path(candidate.parent, root):
        raise WriteRefused(
            "CONFLICT",
            "the review worktree is not this project's review directory",
            {"review_id": ident, "path": str(candidate), "root": str(root)},
        )
    match = _REVIEW_BRANCH.match(branch) if isinstance(branch, str) else None
    if match is None or match.group(2) != ident:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{branch!r} is not a review branch for {ident!r}",
            {"review_id": ident, "branch": branch},
        )
    write_review_progress(
        store,
        ident,
        state="worktree",
        phase="worktree",
        worktree=str(candidate.resolve()),
        branch=branch,
    )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(Path(left).absolute())) == os.path.normcase(
        str(Path(right).absolute())
    )


def _slug(value: Any) -> str:
    if not isinstance(value, str) or not _SLUG.match(value) or len(value) > 80:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{value!r} is not an item slug", {"item": value}
        )
    return value


def _dispatch_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _DISPATCH_ID.match(value)
        or value.upper() in WINDOWS_RESERVED_NAMES
    ):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{value!r} is not a dispatch id",
            {"dispatch_id": value},
        )
    return value

