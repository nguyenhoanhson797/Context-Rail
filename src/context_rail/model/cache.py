"""Parse-once cache for manifest and pack resources.

A model is only reused while its source keeps the same path, modification time
and size; a path-only cache would serve a stale pack after an edit.
"""

from __future__ import annotations

from typing import Any, Callable

_CACHE: dict[tuple[Any, ...], Any] = {}
_MAX_ENTRIES = 256


def cached_model(source: Any, build: Callable[[], Any]) -> Any:
    """Return the cached model for ``source``, rebuilding it if its stat changed."""
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


def _stat_key(source: Any) -> tuple[Any, ...] | None:
    try:
        stat = source.stat()
    except (AttributeError, OSError):
        return None
    try:
        label = str(source.resolve())
    except (AttributeError, OSError):
        label = str(source)
    return (label, stat.st_mtime_ns, stat.st_size)
