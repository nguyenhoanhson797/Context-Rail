"""Pure ISO 8601 helpers shared by the kernel and the store."""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["canonical_iso"]


def canonical_iso(value: str) -> str | None:
    """A timestamp as UTC seconds, so stored strings sort chronologically.

    Naive timestamps are read as UTC. Anything the parser rejects is ``None``:
    a caller never invents a time.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
