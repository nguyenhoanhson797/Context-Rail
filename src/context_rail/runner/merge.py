"""Local merge state: the per-Item merge lock, progress and key bindings.

Merge truth is the Records the handler commits; this module only keeps the
control files that make one merge identifiable and serialised. Everything is
written under ``local_dir``, so no file lands in the repository tree, its git
dir or the store.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from context_rail.core.util import sha256_hex
from context_rail.store.location import local_dir
from context_rail.store.lock import StoreLockTimeout, file_lock
from context_rail.store.write import WriteRefused

__all__ = [
    "acquire_merge_lock",
    "merge_key_binding_path",
    "merge_progress_path",
    "pending_merges",
    "read_merge_binding",
    "read_merge_progress",
    "remove_merge_binding",
    "write_merge_binding",
    "write_merge_progress",
]

MERGE_DIR = "merge"
KEY_DIR = "keys"
LOCK_DIR = "locks"
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_MERGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def merge_progress_path(store: Path, merge_id: str) -> Path:
    """The local control file of one merge attempt."""
    return local_dir(Path(store)) / MERGE_DIR / f"{_merge_id(merge_id)}.json"


def merge_key_binding_path(store: Path, key: str) -> Path:
    """The local file binding one idempotency key to its merge attempt."""
    if not isinstance(key, str) or not key.strip():
        raise WriteRefused("VALIDATION_FAILED", "a merge key must be text")
    digest = sha256_hex(key.encode("utf-8"))
    return local_dir(Path(store)) / MERGE_DIR / KEY_DIR / f"{digest}.json"


@contextlib.contextmanager
def acquire_merge_lock(store: Path, item: str) -> Iterator[None]:
    """Hold the per-Item merge lock; a second live merge is refused."""
    slug = _slug(item)
    path = local_dir(Path(store)) / MERGE_DIR / LOCK_DIR / f"{slug}.lock"
    try:
        with file_lock(path, timeout=0):
            yield
    except StoreLockTimeout:
        raise WriteRefused(
            "CONFLICT",
            f"another merge for {slug!r} is already running",
            {"item": slug, "lock": str(path)},
        ) from None


def pending_merges(store: Path, item: str) -> list[dict[str, Any]]:
    """Every unfinished merge that may still own this Item."""
    slug = _slug(item)
    folder = local_dir(Path(store)) / MERGE_DIR
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append({"merge_id": path.stem, "corrupt": True})
            continue
        if not isinstance(payload, dict):
            found.append({"merge_id": path.stem, "corrupt": True})
            continue
        if payload.get("corrupt"):
            found.append(payload)
            continue
        if payload.get("state") in ("completed", "failed"):
            continue
        if payload.get("item") != slug:
            continue
        found.append(payload)
    return found


def read_merge_progress(store: Path, merge_id: str) -> dict[str, Any] | None:
    """The stored progress of one merge attempt, or None when there is none."""
    path = merge_progress_path(store, merge_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"merge_id": _merge_id(merge_id), "corrupt": True}
    if not isinstance(payload, dict):
        return {"merge_id": merge_id, "corrupt": True}
    return payload


def write_merge_progress(store: Path, merge_id: str, **values: Any) -> dict[str, Any]:
    """Merge one write into the attempt's progress file and return it."""
    ident = _merge_id(merge_id)
    path = merge_progress_path(store, ident)
    current = read_merge_progress(store, ident) or {}
    if current.get("corrupt"):
        current = {"merge_id": ident}
    current.update(values)
    current["merge_id"] = ident
    _write_json(path, current)
    return current


def read_merge_binding(store: Path, key: str) -> dict[str, Any] | None:
    """The binding one merge key already holds, or None."""
    path = merge_key_binding_path(store, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"corrupt": True}
    return payload if isinstance(payload, dict) else {"corrupt": True}


def write_merge_binding(store: Path, key: str, **values: Any) -> dict[str, Any]:
    """Bind one merge key to its payload and attempt id."""
    path = merge_key_binding_path(store, key)
    payload = dict(values)
    _write_json(path, payload)
    return payload


def remove_merge_binding(store: Path, key: str) -> None:
    """Forget a binding whose merge never changed a ref."""
    merge_key_binding_path(store, key).unlink(missing_ok=True)


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


def _merge_id(value: Any) -> str:
    if not isinstance(value, str) or not _MERGE_ID.match(value):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{value!r} is not a merge id", {"merge_id": value}
        )
    return value
