"""Where a store lives and where its local state lives, standard library only.

``local_dir`` locates the git directory by reading ``.git``: a directory in the
main checkout, or a ``gitdir:`` file in a linked worktree. It never launches
git. The lookup is a handful of stats over the store's ancestors on every call,
so a ``.git`` that appears or disappears between calls is seen immediately.

The CLI and the daemon client need this before they may import YAML or SQLite,
so nothing here imports outside the standard library.
"""

from __future__ import annotations

import os
from pathlib import Path

from context_rail.core.slug import sha256_hex, slugify

__all__ = [
    "GIT_SUBDIR",
    "LOCAL_DIR",
    "ROOT_KEY",
    "PathEscape",
    "StoreLocationError",
    "find_store",
    "local_dir",
    "worktree_git_dir",
]

LOCAL_DIR = ".rail-local"
GIT_SUBDIR = "context-rail"
ROOT_KEY = "_root"


class PathEscape(Exception):
    """A store-relative path would land outside the store."""


class StoreLocationError(PathEscape):
    """A ``.git`` marker exists but does not name a usable git directory."""


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


def _worktree_git_dir(store: Path) -> tuple[Path, Path] | None:
    """``(this worktree's git dir, worktree root)``, or None outside git."""
    for directory in (store, *store.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker, directory
        if marker.is_file():
            return _gitdir_target(marker), directory
        if marker.exists() or marker.is_symlink():
            raise StoreLocationError(f"{marker} is not a git directory or gitdir file")
    return None


def _gitdir_target(marker: Path) -> Path:
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise StoreLocationError(f"{marker} cannot be read: {exc}") from None
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        target = value.strip()
        if separator and name.strip().lower() == "gitdir" and target:
            path = Path(target)
            if not path.is_absolute():
                path = marker.parent / path
            found = Path(os.path.normpath(path))
            if not found.is_dir():
                raise StoreLocationError(
                    f"{marker} names a missing git directory: {target!r}"
                )
            return found
    raise StoreLocationError(f"{marker} has no gitdir line")


def _store_key(store: Path, root: Path) -> str:
    """The store's key under the git dir: slug plus a path hash, or ``_root``."""
    try:
        rel = store.relative_to(root).as_posix()
    except ValueError:
        rel = store.name
    if rel in ("", "."):
        return ROOT_KEY
    return f"{slugify(rel)}-{sha256_hex(rel.encode('utf-8'))[:8]}"
