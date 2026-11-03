"""Lifecycle: which status moves may happen, and which category a status carries.

Capability comes from the workflow, so two workflows may share the status
dictionary while allowing different moves.
"""

from __future__ import annotations

from context_rail.model.pack import Pack, Workflow

__all__ = ["category_of", "check_transition"]


def check_transition(workflow: Workflow, current: str, target: str) -> str | None:
    """Return ``None`` when ``current`` may become ``target``, else a reason."""
    if current not in workflow.statuses:
        return f"unknown status {current!r}"
    if target not in workflow.statuses:
        return f"unknown status {target!r}"
    if target in workflow.transitions.get(current, ()):
        return None
    allowed = ", ".join(workflow.transitions.get(current, ())) or "nothing"
    return (
        f"transition {current!r} -> {target!r} is not allowed in workflow "
        f"{workflow.name!r}; allowed: {allowed}"
    )


def category_of(pack: Pack, status: str) -> str | None:
    """The category a status belongs to, or ``None`` for an unknown status."""
    return pack.statuses.get(status)
