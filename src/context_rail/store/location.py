"""Where a store lives, where its local state lives, and path confinement.

``local_dir`` locates the git directory by reading ``.git``: a directory in the
main checkout, or a ``gitdir:`` file in a linked worktree. It never launches
git. The lookup is a handful of stats over the store's ancestors on every call,
so a ``.git`` that appears or disappears between calls is seen immediately.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from context_rail.core.util import sha256_hex, slugify
from context_rail.model.primitives import WINDOWS_RESERVED_NAMES

if TYPE_CHECKING:
    from context_rail.model.manifest import Manifest

LOCAL_DIR = ".rail-local"
GIT_SUBDIR = "context-rail"
ROOT_KEY = "_root"

#: The store's own content, as its documented layout names it. A store rooted at
#: the checkout has no folder of its own, so these are what it owns there.
STORE_ROOTS: tuple[str, ...] = (
    "rail.yaml",
    ".gitignore",
    "items",
    "notes",
    "checks",
    "policies",
    "records",
    "workspace",
    "packs",
    "templates",
    "engine-issues",
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


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


def owns_path(path: str, prefixes: Sequence[str]) -> bool:
    """Whether ``path`` is one of ``prefixes`` or lies below it.

    Every prefix is a literal checkout-relative name: nothing here means "the
    whole tree", so a path a project owns but the store does not stays foreign.
    """
    for prefix in prefixes:
        if prefix and (path == prefix or path.startswith(prefix.rstrip("/") + "/")):
            return True
    return False


def store_prefixes(store: Path, root: Path, manifest: Manifest) -> tuple[str, ...]:
    """The checkout-relative paths the store owns, as literal prefixes.

    A store in its own folder owns that folder. A store rooted at the checkout
    has no folder of its own, so it owns the layout it writes: the manifest's
    own placement patterns and the store's fixed roots. That set is derived from
    the store's structure, never from "everything the project is not".
    """
    try:
        rel = Path(store).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        # A store outside this checkout owns nothing inside it.
        return ()
    if rel not in ("", "."):
        return (rel,)
    owned = set(STORE_ROOTS)
    for template in (*manifest.placement.values(), *manifest.preset_placement.values()):
        first = _first_segment(template)
        if first is not None:
            owned.add(first)
    return tuple(sorted(owned))


def _first_segment(template: str) -> str | None:
    """The literal first segment of a placement pattern, or None when it is a token."""
    head = template.replace("\\", "/").split("/", 1)[0].strip("/")
    if not head or "{" in head or "}" in head:
        return None
    return head


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
        if name.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
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
