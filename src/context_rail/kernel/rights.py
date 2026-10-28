"""Decision rights: which permission an actor may use.

Stage 1a opens read and write to every actor and keeps the human-only kind
closed; the charter Policy narrows this further once it exists.
"""

from __future__ import annotations

__all__ = ["PERMISSIONS", "allowed"]

PERMISSIONS = ("read", "write", "human")

_OPEN = ("read", "write")


def allowed(permission: str, actor: str) -> str | None:
    """Return ``None`` when ``actor`` may use ``permission``, else a reason."""
    if permission not in PERMISSIONS:
        return f"unknown permission {permission!r}"
    if permission in _OPEN:
        return None
    if actor != "human":
        return f"{permission!r} needs actor 'human', not {actor!r}"
    return None
