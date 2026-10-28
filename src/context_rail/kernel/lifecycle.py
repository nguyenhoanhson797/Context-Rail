"""Lifecycle: which status moves may happen, and which category a status carries.

Capability comes from the pack, so a different pack changes the vocabulary
without changing this module.
"""

from __future__ import annotations

from context_rail.model.pack import Pack

__all__ = ["category_of", "check_transition"]


def check_transition(pack: Pack, current: str, target: str) -> str | None:
    """Return ``None`` when ``current`` may become ``target``, else a reason."""
    if current not in pack.statuses:
        return f"unknown status {current!r}"
    if target not in pack.statuses:
        return f"unknown status {target!r}"
    if target in pack.transitions.get(current, ()):
        return None
    allowed = ", ".join(pack.transitions.get(current, ())) or "nothing"
    return f"transition {current!r} -> {target!r} is not allowed; allowed: {allowed}"


def category_of(pack: Pack, status: str) -> str | None:
    """The category a status belongs to, or ``None`` for an unknown status."""
    return pack.statuses.get(status)
