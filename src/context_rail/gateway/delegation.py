"""Audit a charter-delegated action leaves behind: who ran it, for whom.

The charter names the delegate and the action; this record names the human the
charter approval belongs to, so the trail never claims the agent was the human.
"""

from __future__ import annotations

from typing import Any

from context_rail.model.delegation import DELEGATION_USED

#: Tool name -> fixed action id for tools the approved charter may authorize.
DELEGATED_TOOLS = {
    "upgrade": "ops upgrade",
    "adapters sync": "ops adapters render",
    "rollback": "ops rollback",
}

DELEGATION_USED_SCHEMA = 1

__all__ = [
    "DELEGATION_USED",
    "is_delegated",
    "summary",
    "used_record",
]


def is_delegated(ctx: Any, action: str) -> bool:
    """Whether the gateway authorized this call under the charter delegation."""
    return getattr(ctx, "delegated_action", None) == action


def used_record(ctx: Any, action: str) -> dict[str, Any]:
    """The immutable audit record for one delegated execution."""
    approval = ctx.approval
    return {
        "kind": DELEGATION_USED,
        "data": {
            "schema": DELEGATION_USED_SCHEMA,
            "action": action,
            "delegate": ctx.identity.name,
            "approved_by": approval.get("confirmer"),
            "charter_approval": approval.get("latest_record"),
        },
    }


def summary(ctx: Any, action: str) -> dict[str, Any]:
    """The caller-facing facts of a delegated call."""
    approval = ctx.approval
    return {
        "action": action,
        "delegate": ctx.identity.name,
        "approved_by": approval.get("confirmer"),
        "charter_approval": approval.get("latest_record"),
    }
