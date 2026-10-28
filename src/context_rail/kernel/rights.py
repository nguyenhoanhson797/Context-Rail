"""Decision rights: which permission, decision class and reviewer an actor may use.

Stage 1a opens read and write to every actor and keeps the human-only permission
closed. The charter Policy narrows this further: a change is classified from
technical facts, a profile's method list may only narrow its actor's rights, and
the non-delegable human-only floor and change rule cannot be widened by any
profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from context_rail.model.policy import Charter, HUMAN_ONLY_IDS

__all__ = [
    "DECISION_CLASSES",
    "LIFECYCLE_METHODS",
    "PERMISSIONS",
    "ChangeFacts",
    "allowed",
    "allowed_decision",
    "decision_class",
    "review_separation",
]

PERMISSIONS = ("read", "write", "human")
DECISION_CLASSES = ("cto-alone", "cto-reversible", "human-only")
LIFECYCLE_METHODS = ("move",)

_OPEN = ("read", "write")
HUMAN = "human"


@dataclass(frozen=True)
class ChangeFacts:
    """The technical facts that classify one change."""

    method: str
    protected: bool
    acceptance: bool
    decision_id: str | None
    budget_increase: bool


def allowed(permission: str, actor: str) -> str | None:
    """Return ``None`` when ``actor`` may use ``permission``, else a reason."""
    if permission not in PERMISSIONS:
        return f"unknown permission {permission!r}"
    if permission in _OPEN:
        return None
    if actor != HUMAN:
        return f"{permission!r} needs actor {HUMAN!r}, not {actor!r}"
    return None


def decision_class(method: str, change: ChangeFacts, policy: Charter) -> str:
    """The class one change belongs to, from facts and never from a preset or tag."""
    if change.acceptance or change.budget_increase:
        return "human-only"
    protected_change = change.protected and method not in LIFECYCLE_METHODS
    if change.decision_id is None:
        return "human-only" if protected_change else "cto-alone"
    if change.decision_id in HUMAN_ONLY_IDS or change.decision_id in policy.human_in_loop:
        return "human-only"
    named = _class_of(change.decision_id, policy.decision_rights)
    if named is None or named == "human-only" or protected_change:
        return "human-only"
    return named


def allowed_decision(
    permission: str,
    decision: str,
    actor: str,
    policy: Charter,
    allowed_methods: tuple[str, ...] | None,
    approved: bool,
) -> str | None:
    """Return ``None`` when the actor may make the decision, else a reason.

    ``allowed_methods`` is a profile's method list; it narrows writes and never
    widens reads. ``approved`` gates writes on an approved charter, so nothing
    executes before the human approves while planning and reads stay open.
    """
    refusal = allowed(permission, actor)
    if refusal is not None:
        return refusal
    if decision not in DECISION_CLASSES:
        return f"unknown decision class {decision!r}"
    if allowed_methods is not None and permission != "read" and permission not in allowed_methods:
        listed = ", ".join(allowed_methods) or "none"
        return f"the profile does not allow {permission!r}; it lists: {listed}"
    if actor != HUMAN:
        if permission == "write" and not approved:
            return "the charter is not approved yet"
        if decision == "human-only":
            return f"a human-only decision cannot be delegated to {actor!r}"
        if permission == "write" and not policy.change_rule.get("approval_required", True):
            return "the charter change rule does not require approval"
        if permission == "write" and policy.change_rule.get("delegable", False):
            return f"the charter change rule does not delegate this write to {actor!r}"
    return None


def review_separation(
    implementer: str,
    reviewer: str,
    implementer_model: str,
    reviewer_model: str,
    require_different_model: bool,
) -> str | None:
    """Return ``None`` when the review is separated, else the reason it is not."""
    if not implementer or not reviewer:
        return "both the implementer and the reviewer must be named"
    if implementer == reviewer:
        return f"the reviewer must be a different actor from the implementer, not {reviewer!r}"
    if require_different_model:
        if not implementer_model or not reviewer_model:
            return "both models must be named when the charter requires a different reviewer model"
        if implementer_model == reviewer_model:
            return f"the reviewer must use a different model from {implementer_model!r}"
    return None


def _class_of(decision_id: str, rights: dict[str, tuple[str, ...]]) -> str | None:
    for name in DECISION_CLASSES:
        if decision_id in rights.get(name, ()):
            return name
    return None
