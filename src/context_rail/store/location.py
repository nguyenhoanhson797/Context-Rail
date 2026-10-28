"""Where a store lives, where its local state lives, and path confinement.

``local_dir`` locates the git directory by reading ``.git``: a directory in the
main checkout, or a ``gitdir:`` file in a linked worktree. It never launches
git. The lookup is a handful of stats over the store's ancestors on every call,
so a ``.git`` that appears or disappears between calls is seen immediately.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from context_rail.core.util import sha256_hex, slugify

LOCAL_DIR = ".rail-local"
GIT_SUBDIR = "context-rail"
ROOT_KEY = "_root"

_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PathEscape(Exception):
    """A store-relative path would land outside the store."""


def find_store(start: Path) -> Path | None:
    """The store containing ``start``, or None when no ancestor holds one."""
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        if (directory / "rail.yaml").is_file():
            return directory
        nested = directory / GIT_SUBDIR
        if (nested / "rail.yaml").is_file():
            return nested
    return None


def local_dir(store: Path) -> Path:
    """The folder holding this store's index and lock (never created here).

    Inside a git worktree it is ``<this worktree's git dir>/context-rail/<key>``
    so two worktrees of one repo never share state; outside git it is
    ``<store>/.rail-local``.
    """
    store = Path(store)
    found = worktree_git_dir(store.absolute())
    if found is None:
        return store / LOCAL_DIR
    git_dir, root = found
    return git_dir / GIT_SUBDIR / _store_key(store.absolute(), root)


def worktree_git_dir(store: Path) -> tuple[Path, Path] | None:
    """``(this worktree's git dir, worktree root)``, or None outside git.

    The git dir is found by reading ``.git`` in the store or an ancestor; git
    is never run.
    """
    return _worktree_git_dir(Path(store).absolute())


def index_path(store: Path) -> Path:
    """The SQLite index file for ``store``."""
    return local_dir(store) / "index.sqlite3"


def confine(store: Path, rel: str) -> Path:
    """The resolved absolute path of ``rel`` inside ``store``.

    Every escape class is refused before touching the filesystem, and the
    resolved path (symlinks followed) must stay inside the store.
    """
    if not isinstance(rel, str):
        raise PathEscape(f"a store path must be text, not {type(rel).__name__}")
    if rel == "":
        raise PathEscape("an empty store path has no target")
    if "\x00" in rel:
        raise PathEscape(f"NUL byte in {rel!r}")
    text = rel.replace("\\", "/")
    if text.startswith("/") or _DRIVE_RE.match(text):
        raise PathEscape(f"{rel!r} is absolute, rooted, UNC or drive-qualified")
    segments = text.split("/")
    for segment in segments:
        if segment.startswith("..") and not segment.lstrip(".").strip(" ."):
            raise PathEscape(f"parent segment in {rel!r}")
        name = segment.rstrip(" .")
        if name.split(".", 1)[0].upper() in _RESERVED_NAMES:
            raise PathEscape(f"{segment!r} is a reserved name")
    root = Path(store).resolve()
    target = root.joinpath(*[s for s in segments if s not in ("", ".")]).resolve()
    if not target.is_relative_to(root):
        raise PathEscape(f"{rel!r} resolves outside the store")
    return target


def _worktree_git_dir(store: Path) -> tuple[Path, Path] | None:
    """``(this worktree's git dir, worktree root)``, or None outside git."""
    for directory in (store, *store.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker, directory
        if marker.is_file():
            git_dir = _gitdir_target(marker)
            if git_dir is not None:
                return git_dir, directory
    return None


def _gitdir_target(marker: Path) -> Path | None:
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        target = value.strip()
        if separator and name.strip().lower() == "gitdir" and target:
            path = Path(target)
            if not path.is_absolute():
                path = marker.parent / path
            return Path(os.path.normpath(path))
    return None


def _store_key(store: Path, root: Path) -> str:
    """The store's key under the git dir: slug plus a path hash, or ``_root``."""
    try:
        rel = store.relative_to(root).as_posix()
    except ValueError:
        rel = store.name
    if rel in ("", "."):
        return ROOT_KEY
    return f"{slugify(rel)}-{sha256_hex(rel.encode('utf-8'))[:8]}"
