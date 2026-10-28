"""Local dispatch state: worktrees, branches, locks and progress.

Dispatch truth is a pair of immutable Records; this module only keeps the
control files that make a launch survivable: where its worktree lives, which
phase it reached, and the key it was started with. Everything here is written
under ``local_dir`` (or the OS temporary directory for the checkout itself), so
nothing lands in the repository tree, its git dir or the store.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.core.util import sha256_hex
from context_rail.store.location import local_dir, worktree_git_dir
from context_rail.store.lock import StoreLockTimeout, file_lock
from context_rail.store.write import WriteRefused

__all__ = [
    "DispatchPaths",
    "PROGRESS_STATES",
    "acquire_dispatch_lock",
    "dispatch_paths",
    "key_binding_path",
    "pending_dispatches",
    "progress_path",
    "project_root",
    "read_binding",
    "read_progress",
    "read_review_binding",
    "read_review_progress",
    "register_review_worktree",
    "register_worktree",
    "remove_binding",
    "remove_review_binding",
    "review_binding_path",
    "review_paths",
    "review_progress_path",
    "write_binding",
    "write_progress",
    "write_review_binding",
    "write_review_progress",
    "worktree_root",
]

DISPATCH_DIR = "dispatch"
REVIEW_DIR = "review"
KEY_DIR = "keys"
LOCK_DIR = "locks"
WORKTREE_PARENT = "context-rail-worktrees"
PROGRESS_STATES = ("started", "worktree", "running", "verifying", "completed")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_BRANCH = re.compile(r"^rail/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_REVIEW_BRANCH = re.compile(
    r"^rail-review/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})$"
)
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.I)


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
            {"store": str(store)},
        )
    return located[1]


def worktree_root(store: Path) -> Path:
    """``<temp>/context-rail-worktrees/<project-location-hash>``.

    The hash is derived from the canonical project path, so the same project
    keeps one root across processes while two projects never share one.
    """
    root = project_root(store).resolve()
    location = os.path.normcase(str(root))
    digest = sha256_hex(location.encode("utf-8"))[:12]
    return Path(tempfile.gettempdir()) / WORKTREE_PARENT / digest


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
    path = progress_path(store, dispatch_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"dispatch_id": _dispatch_id(dispatch_id), "corrupt": True}
    return payload if isinstance(payload, dict) else {"dispatch_id": dispatch_id, "corrupt": True}


def write_progress(store: Path, dispatch_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into one dispatch's progress file and return it."""
    ident = _dispatch_id(dispatch_id)
    path = progress_path(store, ident)
    current = read_progress(store, ident) or {}
    if current.get("corrupt"):
        current = {"dispatch_id": ident, "corrupt": True}
    merged = {**current, **values, "schema": 1, "dispatch_id": ident}
    _write_json(path, merged)
    return merged


def read_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The dispatch id one key already names, or None."""
    path = key_binding_path(store, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"key": key, "corrupt": True}
    return payload if isinstance(payload, dict) else {"key": key, "corrupt": True}


def read_review_progress(store: Path, review_id: str) -> dict[str, Any] | None:
    """The stored progress of one review, or None when there is none."""
    path = review_progress_path(store, review_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"dispatch_id": _dispatch_id(review_id), "corrupt": True}
    return payload if isinstance(payload, dict) else {"dispatch_id": review_id, "corrupt": True}


def write_review_progress(store: Path, review_id: str, **values: Any) -> dict[str, Any]:
    """Merge ``values`` into one review's progress file and return it."""
    ident = _dispatch_id(review_id)
    path = review_progress_path(store, ident)
    current = read_review_progress(store, ident) or {}
    if current.get("corrupt"):
        current = {"dispatch_id": ident, "corrupt": True}
    merged = {**current, **values, "schema": 1, "dispatch_id": ident}
    _write_json(path, merged)
    return merged


def read_review_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The review id one key already names, or None."""
    path = review_binding_path(store, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"key": key, "corrupt": True}
    return payload if isinstance(payload, dict) else {"key": key, "corrupt": True}


def write_review_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Persist the key-to-review binding before any branch is created."""
    path = review_binding_path(store, key)
    payload = {**values, "schema": 1, "key": key}
    _write_json(path, payload)
    return payload


def remove_review_binding(store: Path, key: str) -> None:
    """Forget a review binding whose launch never started."""
    review_binding_path(store, key).unlink(missing_ok=True)


def write_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Persist the key-to-dispatch binding before any branch is created."""
    path = key_binding_path(store, key)
    payload = {**values, "schema": 1, "key": key}
    _write_json(path, payload)
    return payload


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
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append({"dispatch_id": path.stem, "corrupt": True, "phase": "unknown"})
            continue
        if not isinstance(payload, dict):
            found.append({"dispatch_id": path.stem, "corrupt": True, "phase": "unknown"})
            continue
        if payload.get("state") == "completed":
            continue
        if payload.get("item") not in (None, item):
            continue
        found.append(payload)
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


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
        or _RESERVED.match(value)
    ):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{value!r} is not a dispatch id",
            {"dispatch_id": value},
        )
    return value
