"""Charter-approved delegation of named operational actions to one lead.

The delegation executes a decision the human already made; it never makes a new
one. The block is part of the charter's semantic picture, so changing it needs a
new approval, and the action list is fixed here: an unknown action is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from context_rail.model.fields import (
    mapping,
    no_unknown,
    refuse,
    require,
    string,
    string_list,
)
from context_rail.model.roles import MAX_NAME_LENGTH, is_name

__all__ = [
    "DELEGABLE_ACTIONS",
    "DELEGATION_KEYS",
    "DELEGATION_USED",
    "Delegation",
    "parse_delegation",
]

#: Operations a charter may delegate. Each executes an approved decision and
#: leaves a record; actions that create or replace the boundary stay human.
DELEGABLE_ACTIONS = (
    "ops upgrade",
    "ops adapters render",
    "ops rollback",
)
DELEGATION_KEYS = ("to", "actions")
#: The engine-owned record every delegated execution leaves behind.
DELEGATION_USED = "delegation.used"
_WHAT = "charter.delegation"


@dataclass(frozen=True)
class Delegation:
    """One named delegate and the operational actions it may execute."""

    to: str
    actions: tuple[str, ...]

    def allows(self, action: str) -> bool:
        """Whether this delegation names ``action``."""
        return action in self.actions


def parse_delegation(raw: Any) -> Delegation:
    """Validate a charter's delegation block, refusing every unknown field."""
    data = mapping(raw, _WHAT)
    no_unknown(data, DELEGATION_KEYS, _WHAT)
    require(data, DELEGATION_KEYS, _WHAT)
    to = string(data["to"], f"{_WHAT}.to")
    if to == "human" or not is_name(to):
        refuse(
            f"{_WHAT}.to {to!r} is not a delegate name; "
            f"use a lowercase slug of at most {MAX_NAME_LENGTH} characters"
        )
    actions = string_list(data["actions"], f"{_WHAT}.actions")
    if len(set(actions)) != len(actions):
        refuse(f"{_WHAT}.actions must not repeat an action")
    unknown = sorted(set(actions) - set(DELEGABLE_ACTIONS))
    if unknown:
        refuse(
            f"{_WHAT}.actions names an action that cannot be delegated: "
            + ", ".join(unknown)
        )
    return Delegation(to=to, actions=actions)
