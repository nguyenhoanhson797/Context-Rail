"""Where a store lives, where its local state lives, and path confinement.

Finding a store and its local directory is standard-library work and lives in
``core.locate``; the names are re-exported here for every caller that already
imports them from the store layer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from context_rail.core.locate import (
    GIT_SUBDIR,
    LOCAL_DIR,
    ROOT_KEY,
    PathEscape,
    StoreLocationError,
    find_store,
    local_dir,
    worktree_git_dir,
)
from context_rail.model.primitives import WINDOWS_RESERVED_NAMES

if TYPE_CHECKING:
    from context_rail.model.manifest import Manifest

__all__ = [
    "GIT_SUBDIR",
    "LOCAL_DIR",
    "ROOT_KEY",
    "STORE_ROOTS",
    "PathEscape",
    "StoreLocationError",
    "confine",
    "find_store",
    "index_path",
    "local_dir",
    "owns_path",
    "store_prefixes",
    "worktree_git_dir",
]

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
