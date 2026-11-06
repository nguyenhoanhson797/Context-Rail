"""Parse-once cache for manifest, pack and compiled project models.

A model is only reused while its source keeps the same path, modification time,
size and content digest. Timestamps tick coarsely, so a same-size edit made
within one tick would otherwise be served stale by a long-lived process. The
compiled project cache is keyed by semantic and presentation digests, never
by path identity, so an edit that changes a digest can never serve the old
model.
"""

from __future__ import annotations

from typing import Any, Callable

from context_rail.core.slug import sha256_hex

_CACHE: dict[tuple[Any, ...], Any] = {}
_CACHE_PROJECT: dict[tuple[Any, ...], Any] = {}
_MAX_ENTRIES = 256
_MAX_PROJECT_ENTRIES = 64


def cached_model(source: Any, build: Callable[[], Any]) -> Any:
    """Return the cached model for ``source``, rebuilding it if its content changed."""
    key = _stat_key(source)
    if key is None:
        return build()
    try:
        return _CACHE[key]
    except KeyError:
        value = build()
        if len(_CACHE) >= _MAX_ENTRIES:
            for stale in list(_CACHE)[: _MAX_ENTRIES // 2]:
                del _CACHE[stale]
        _CACHE[key] = value
        return value


def cached_project(pack: Any, presentation: Any) -> Any:
    """One compiled ``ProjectModel`` per (semantic, presentation) digest.

    Pure: never opens the index, executes a command or hashes a source tree.
    The key is the two canonical digests plus the pack name, so a pack edit
    that changes semantics or a ``surface.yaml`` edit that changes labels
    always rebuilds, while an unchanged invocation reuses the frozen IR.
    """
    from context_rail.model.compiler import compile_project, semantic_digest
    from context_rail.model.presentation import presentation_hash

    key = (pack.name, semantic_digest(pack), presentation_hash(presentation))
    try:
        return _CACHE_PROJECT[key]
    except KeyError:
        value = compile_project(pack, pack.operating, presentation)
        if len(_CACHE_PROJECT) >= _MAX_PROJECT_ENTRIES:
            for stale in list(_CACHE_PROJECT)[: _MAX_PROJECT_ENTRIES // 2]:
                del _CACHE_PROJECT[stale]
        _CACHE_PROJECT[key] = value
        return value


def clear_project_cache() -> None:
    """Drop every cached compiled project (used by tests)."""
    _CACHE_PROJECT.clear()


def _stat_key(source: Any) -> tuple[Any, ...] | None:
    try:
        stat = source.stat()
        content = sha256_hex(source.read_bytes())
    except (AttributeError, OSError):
        return None
    try:
        label = str(source.resolve())
    except (AttributeError, OSError):
        label = str(source)
    return (label, stat.st_mtime_ns, stat.st_size, content)
