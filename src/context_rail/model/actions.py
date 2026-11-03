"""Declarative pack actions: a named, parameterised call to one safe tool."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from context_rail.core.tool_names import canonical_tool
from context_rail.model.errors import ModelError

__all__ = [
    "ACTIONS_KEYS",
    "Action",
    "Requires",
    "parse_actions",
]

ACTIONS_KEYS = ("name", "on", "from", "requires", "tool", "args", "use_when", "version")
REQUIRES_KEYS = ("children", "check", "review", "field")
WRITE_ALLOWLIST = frozenset(
    {"add", "update", "link", "move", "rename", "check", "triage", "report"}
)
FORBIDDEN_TOOLS = frozenset(
    {"do", "cto tick", "pack use", "relocate", "merge", "adapters sync"}
)
RESERVED_ARGS = frozenset({"path", "actor", "key", "profile", "store", "worktree"})
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _refuse(message: str) -> None:
    raise ModelError("VALIDATION_FAILED", message)


@dataclass(frozen=True)
class Requires:
    """The preconditions an action checks before it calls its tool."""

    children: bool = False
    check: str | None = None
    review: bool = False
    field: str | None = None


@dataclass(frozen=True)
class Action:
    """One pack verb: what it applies to, what it needs, and the tool it calls."""

    name: str
    on: str
    from_states: tuple[str, ...]
    requires: Requires
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    use_when: str = ""
    version: int = 1


def parse_actions(
    raw: Any,
    statuses: Mapping[str, str],
    presets: Mapping[str, Any],
    workflows: Mapping[str, Any],
) -> dict[str, Action]:
    """Parse the pack's named actions, refusing anything it could not run safely."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        _refuse("actions must be a mapping")
    actions: dict[str, Action] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            _refuse(f"invalid action name {name!r}")
        if name in actions:
            _refuse(f"duplicate action {name!r}")
        if not isinstance(value, Mapping):
            _refuse(f"action {name!r} must be a mapping")
        unknown = sorted(set(value) - set(ACTIONS_KEYS))
        if unknown:
            _refuse(f"action {name!r} has unknown key(s): " + ", ".join(unknown))
        actions[name] = _action(name, value, statuses, presets, workflows)
    return actions


def _action(
    name: str,
    value: Mapping[str, Any],
    statuses: Mapping[str, str],
    presets: Mapping[str, Any],
    workflows: Mapping[str, Any],
) -> Action:
    on = value.get("on")
    if not isinstance(on, str) or (on != "*" and on not in presets):
        _refuse(f"action {name!r} names unknown preset {on!r}")
    states = _states(value.get("from", []), statuses, f"action {name!r} from")
    if on != "*":
        workflow = workflows[presets[on].workflow]
        unknown = [state for state in states if state not in workflow.transitions]
        if unknown:
            _refuse(
                f"action {name!r} starts in status(es) outside its workflow: "
                + ", ".join(unknown)
            )
    tool = value.get("tool")
    if not isinstance(tool, str) or not tool:
        _refuse(f"action {name!r} needs a tool")
    canonical = canonical_tool(tool)
    if canonical in FORBIDDEN_TOOLS:
        _refuse(f"action {name!r} may not call {tool!r}")
    if canonical not in WRITE_ALLOWLIST:
        _refuse(f"action {name!r} calls a tool outside the allowlist: {tool!r}")
    args = _args(value.get("args", {}), name)
    use_when = value.get("use_when")
    if not isinstance(use_when, str) or not use_when.strip():
        _refuse(f"action {name!r} needs a non-empty use_when")
    version = value.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _refuse(f"action {name!r} version must be a positive integer")
    return Action(
        name=name,
        on=on,
        from_states=tuple(states),
        requires=_requires(value.get("requires", {}), name),
        tool=canonical,
        args=args,
        use_when=use_when.strip(),
        version=version,
    )


def _states(raw: Any, statuses: Mapping[str, str], what: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        _refuse(f"{what} must be a non-empty list of statuses")
    values: list[str] = []
    for state in raw:
        if not isinstance(state, str) or state not in statuses:
            _refuse(f"{what} names unknown status {state!r}")
        if state not in values:
            values.append(state)
    return values


def _requires(raw: Any, action: str) -> Requires:
    if not isinstance(raw, Mapping):
        _refuse(f"action {action!r} requires must be a mapping")
    unknown = sorted(set(raw) - set(REQUIRES_KEYS))
    if unknown:
        _refuse(f"action {action!r} requires has unknown key(s): " + ", ".join(unknown))
    children = raw.get("children", False)
    review = raw.get("review", False)
    check = raw.get("check")
    field_name = raw.get("field")
    if not isinstance(children, bool) or not isinstance(review, bool):
        _refuse(f"action {action!r} children and review must be true or false")
    if check is not None and (not isinstance(check, str) or not check):
        _refuse(f"action {action!r} check must be a check name")
    if field_name is not None and (
        not isinstance(field_name, str) or not _NAME.fullmatch(field_name)
    ):
        _refuse(f"action {action!r} field must be a safe field name")
    return Requires(
        children=children, check=check, review=review, field=field_name
    )


def _args(raw: Any, action: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        _refuse(f"action {action!r} args must be a mapping")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            _refuse(f"action {action!r} args keys must be names")
        if key in RESERVED_ARGS:
            _refuse(f"action {action!r} may not set {key!r}")
        if isinstance(value, str):
            for placeholder in _PLACEHOLDER.findall(value):
                if placeholder in RESERVED_ARGS:
                    _refuse(
                        f"action {action!r} may not template {placeholder!r}"
                    )
        elif isinstance(value, (int, float, bool)):
            pass
        else:
            _refuse(f"action {action!r} arg {key!r} must be a scalar or template")
        values[key] = value
    return values
