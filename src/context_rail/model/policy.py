"""The charter Policy: gates, decision rights, budgets, thresholds and answers.

A charter is a Policy document whose meaning the engine enforces. It fails
closed: an unknown field, a fifth gate, a disabled change rule, a shrunken
human-only floor or a finite-number threshold that is not finite is refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from context_rail.model.errors import ModelError
from context_rail.model.fields import (
    boolean,
    finite_number,
    integer,
    mapping,
    no_unknown,
    refuse,
    relative_path,
    require,
    schema_version,
    string,
    string_list,
)
from context_rail.model.primitives import CHARTER_KEYS

__all__ = [
    "CHARTER_SCHEMA",
    "DECISION_RIGHTS",
    "DECISION_CLASSES",
    "GATES",
    "GATE_KEYS",
    "HUMAN_ONLY_IDS",
    "ANSWERS_KEYS",
    "Charter",
    "Gate",
    "ModelError",
    "parse_answers",
    "parse_charter",
    "parse_project",
    "quality_problems",
]

CHARTER_SCHEMA = 1
ANSWERS_SCHEMA = 1
GATES = ("item_done", "scope_done", "merge", "release")
GATE_KEYS = ("require_checks", "require_acceptance", "require_review", "require_human")
_GATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DECISION_RIGHTS: dict[str, tuple[str, ...]] = {
    "cto-alone": (
        "breakdown",
        "ordering",
        "assignment",
        "technical_design",
        "refactor",
        "tests",
        "retry",
        "close",
    ),
    "cto-reversible": (
        "dependency",
        "data_model",
        "api",
        "convention",
        "drop_could",
        "near_budget",
    ),
    "human-only": (
        "goal",
        "must_should",
        "wont",
        "acceptance",
        "taste",
        "publish",
        "deploy",
        "delete_data",
        "rewrite_history",
        "force_push",
        "tag",
        "release",
        "license",
        "secrets",
        "payments",
        "budget_increase",
        "stuck_exhausted",
        "charter",
    ),
}
DECISION_CLASSES = tuple(DECISION_RIGHTS)
HUMAN_ONLY_IDS = DECISION_RIGHTS["human-only"]
ALL_DECISION_IDS = tuple(
    decision_id for ids in DECISION_RIGHTS.values() for decision_id in ids
)

ANSWERS_KEYS = ("schema", "goal", "vision", "scope", "wont", "charter", "pack")
SCOPE_PRIORITIES = ("must", "should", "could")
CHANGE_RULE = {"approval_required": True, "delegable": False}
DEFAULT_BUDGETS: dict[str, dict[str, Any]] = {
    "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
    "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
}
BUDGET_SCOPES = ("item", "project")
BUDGET_KEYS = ("attempts", "seconds", "tokens", "credits")
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "stuck_hours": 24,
    "near_budget_ratio": 0.8,
    "triage_reports": 2,
    "triage_weight": 2,
}
THRESHOLD_KEYS = ("stuck_hours", "near_budget_ratio", "triage_reports", "triage_weight")
_DEFAULT_GATES = {
    "item_done": (True, False, True, False),
    "scope_done": (True, True, True, False),
    "merge": (True, False, True, False),
    "release": (True, True, True, True),
}


@dataclass(frozen=True)
class Gate:
    """One gate: which proofs a target needs before it may advance."""

    require_checks: bool
    require_acceptance: bool
    require_review: bool
    require_human: bool


@dataclass(frozen=True)
class Charter:
    """A validated charter Policy document."""

    schema: int
    project: dict[str, Any]
    protected_files: tuple[str, ...]
    gates: dict[str, Gate]
    human_in_loop: tuple[str, ...]
    budgets: dict[str, dict[str, Any]]
    thresholds: dict[str, Any]
    change_rule: dict[str, bool]
    decision_rights: dict[str, tuple[str, ...]]
    default_branch: str
    remote: str
    allow_push: bool
    reviewer_different_model: bool


def parse_charter(fields: dict[str, Any]) -> Charter:
    """Validate charter fields, filling in the pinned defaults."""
    data = mapping(fields, "charter")
    schema = schema_version(data, CHARTER_SCHEMA, "charter")
    no_unknown(data, CHARTER_KEYS, "charter")
    require(data, ("project", "default_branch"), "charter")

    project = parse_project(data["project"])
    protected_files = tuple(
        dict.fromkeys(
            relative_path(path, "charter.protected_files")
            for path in _list(data.get("protected_files", []), "charter.protected_files")
        )
    )
    gates = _gates(data.get("gates"))
    decision_rights = _decision_rights(data.get("decision_rights"))
    human_in_loop = _human_in_loop(data.get("human_in_loop"), decision_rights)
    budgets = _budgets(data.get("budgets"))
    thresholds = _thresholds(data.get("thresholds"))
    change_rule = _change_rule(data.get("change_rule"))

    return Charter(
        schema=schema,
        project=project,
        protected_files=protected_files,
        gates=gates,
        human_in_loop=human_in_loop,
        budgets=budgets,
        thresholds=thresholds,
        change_rule=change_rule,
        decision_rights=decision_rights,
        default_branch=string(data["default_branch"], "charter.default_branch"),
        remote=string(data.get("remote", "origin"), "charter.remote"),
        allow_push=boolean(data.get("allow_push", False), "charter.allow_push"),
        reviewer_different_model=boolean(
            data.get("reviewer_different_model", True),
            "charter.reviewer_different_model",
        ),
    )


def parse_project(value: dict[str, Any]) -> dict[str, Any]:
    """Validate ``charter.project``: goal, vision, scope slugs and the Won't list."""
    data = mapping(value, "charter.project")
    no_unknown(data, ("goal", "vision", "scope", "wont"), "charter.project")
    require(data, ("goal", "vision", "scope", "wont"), "charter.project")
    goal = string(data["goal"], "charter.project.goal")
    vision = string(data["vision"], "charter.project.vision")
    scope = list(string_list(data["scope"], "charter.project.scope", allow_empty=True))
    wont = list(string_list(data["wont"], "charter.project.wont", allow_empty=True))
    if len(set(scope)) != len(scope):
        refuse("charter.project.scope must not repeat a slug")
    return {"goal": goal, "vision": vision, "scope": scope, "wont": wont}


def parse_answers(data: dict[str, Any]) -> dict[str, Any]:
    """Validate an interview/draft answers file and return the picture it names."""
    payload = mapping(data, "answers")
    schema = schema_version(payload, ANSWERS_SCHEMA, "answers")
    no_unknown(payload, ANSWERS_KEYS, "answers")
    require(payload, ("goal", "vision", "scope", "wont", "charter", "pack"), "answers")

    goal = _piece(payload["goal"], "answers.goal", ("title", "body"))
    vision = _piece(payload["vision"], "answers.vision", ("title", "body"))
    scope = _scope(payload["scope"])
    if not any(entry["priority"] == "must" for entry in scope):
        refuse("answers.scope needs at least one must entry")
    wont = _wont(payload["wont"])

    return {
        "schema": schema,
        "goal": goal,
        "vision": vision,
        "scope": scope,
        "wont": wont,
        "charter": parse_charter(payload["charter"]),
        "pack": string(payload["pack"], "answers.pack"),
    }


def _gates(raw: Any) -> dict[str, Gate]:
    if raw is None:
        return {
            name: Gate(*_DEFAULT_GATES[name])
            for name in GATES
        }
    data = mapping(raw, "charter.gates")
    for name in data:
        if name not in GATES and not _GATE_NAME.fullmatch(name):
            refuse(
                f"charter.gates has a malformed gate name {name!r}"
            )
    require(data, GATES, "charter.gates")
    gates: dict[str, Gate] = {}
    for name, value in data.items():
        entry = mapping(value, f"charter.gates.{name}")
        no_unknown(entry, GATE_KEYS, f"charter.gates.{name}")
        require(entry, GATE_KEYS, f"charter.gates.{name}")
        gates[name] = Gate(
            *(boolean(entry[key], f"charter.gates.{name}.{key}") for key in GATE_KEYS)
        )
    return gates


def quality_problems(pack: Any, charter: Charter) -> list[str]:
    """Every workflow quality rule that names a gate the charter does not declare."""
    problems: list[str] = []
    for workflow in pack.workflows.values():
        for status, rule in workflow.quality.items():
            if rule.gate not in charter.gates:
                problems.append(
                    f"workflow {workflow.name!r} status {status!r} names "
                    f"unknown gate {rule.gate!r}"
                )
    return problems


def _decision_rights(raw: Any) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return dict(DECISION_RIGHTS)
    data = mapping(raw, "charter.decision_rights")
    no_unknown(data, DECISION_CLASSES, "charter.decision_rights")
    require(data, DECISION_CLASSES, "charter.decision_rights")

    rights: dict[str, tuple[str, ...]] = {}
    for name in DECISION_CLASSES:
        values = string_list(
            data[name], f"charter.decision_rights.{name}", allow_empty=True
        )
        duplicated = sorted({value for value in values if values.count(value) > 1})
        if duplicated:
            refuse(
                f"charter.decision_rights.{name} repeats " + ", ".join(duplicated)
            )
        moved = sorted(set(values) - set(DECISION_RIGHTS[name]))
        if moved:
            refuse(
                f"charter.decision_rights.{name} claims decision(s) outside its class: "
                + ", ".join(moved)
            )
        rights[name] = values

    missing_floor = sorted(set(HUMAN_ONLY_IDS) - set(rights["human-only"]))
    if missing_floor:
        refuse(
            "charter.decision_rights.human-only cannot drop "
            + ", ".join(missing_floor)
        )
    return rights


def _human_in_loop(raw: Any, rights: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    if raw is None:
        return rights["human-only"]
    values = string_list(raw, "charter.human_in_loop", allow_empty=True)
    unknown = sorted(set(values) - set(ALL_DECISION_IDS))
    if unknown:
        refuse("charter.human_in_loop names unknown decision(s): " + ", ".join(unknown))
    missing_floor = sorted(set(HUMAN_ONLY_IDS) - set(values))
    if missing_floor:
        refuse(
            "charter.human_in_loop cannot drop the human-only floor: "
            + ", ".join(missing_floor)
        )
    return tuple(dict.fromkeys(values))


def _budgets(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {scope: dict(values) for scope, values in DEFAULT_BUDGETS.items()}
    data = mapping(raw, "charter.budgets")
    no_unknown(data, BUDGET_SCOPES, "charter.budgets")
    budgets: dict[str, dict[str, Any]] = {}
    for scope in BUDGET_SCOPES:
        entry = mapping(
            data[scope] if scope in data else DEFAULT_BUDGETS[scope], f"charter.budgets.{scope}"
        )
        no_unknown(entry, BUDGET_KEYS, f"charter.budgets.{scope}")
        budgets[scope] = {
            "attempts": _optional_number(
                entry.get("attempts"), f"charter.budgets.{scope}.attempts", integer_only=True
            ),
            "seconds": _optional_number(
                entry.get("seconds"), f"charter.budgets.{scope}.seconds"
            ),
            "tokens": _optional_number(
                entry.get("tokens"), f"charter.budgets.{scope}.tokens"
            ),
            "credits": _optional_number(
                entry.get("credits"), f"charter.budgets.{scope}.credits"
            ),
        }
    return budgets


def _optional_number(value: Any, what: str, *, integer_only: bool = False) -> Any:
    if value is None:
        return None
    if integer_only:
        return integer(value, what, positive=True)
    return finite_number(value, what, positive=True)


def _thresholds(raw: Any) -> dict[str, Any]:
    if raw is None:
        return dict(DEFAULT_THRESHOLDS)
    data = mapping(raw, "charter.thresholds")
    no_unknown(data, THRESHOLD_KEYS, "charter.thresholds")
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update(data)
    return {
        "stuck_hours": finite_number(
            merged["stuck_hours"], "charter.thresholds.stuck_hours", minimum=0
        ),
        "near_budget_ratio": finite_number(
            merged["near_budget_ratio"], "charter.thresholds.near_budget_ratio", positive=True
        ),
        "triage_reports": integer(
            merged["triage_reports"], "charter.thresholds.triage_reports", positive=True
        ),
        "triage_weight": integer(
            merged["triage_weight"], "charter.thresholds.triage_weight", positive=True
        ),
    }


def _change_rule(raw: Any) -> dict[str, bool]:
    if raw is None:
        return dict(CHANGE_RULE)
    data = mapping(raw, "charter.change_rule")
    no_unknown(data, ("approval_required", "delegable"), "charter.change_rule")
    approval = boolean(data.get("approval_required"), "charter.change_rule.approval_required")
    delegable = boolean(data.get("delegable"), "charter.change_rule.delegable")
    if not approval or delegable:
        refuse("charter.change_rule can never be disabled or delegated")
    return {"approval_required": approval, "delegable": delegable}


def _piece(raw: Any, what: str, keys: tuple[str, ...]) -> dict[str, str]:
    data = mapping(raw, what)
    no_unknown(data, keys, what)
    require(data, keys, what)
    result = {key: data[key] for key in keys}
    result["title"] = string(result["title"], f"{what}.title")
    result["body"] = _text(result["body"], f"{what}.body")
    return result


def _scope(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        refuse("answers.scope must be a non-empty list")
    entries: list[dict[str, str]] = []
    for entry in raw:
        data = mapping(entry, "answers.scope entry")
        no_unknown(data, ("title", "body", "priority"), "answers.scope entry")
        require(data, ("title", "body", "priority"), "answers.scope entry")
        priority = string(data["priority"], "answers.scope.priority")
        if priority not in SCOPE_PRIORITIES:
            refuse(
                "answers.scope.priority must be one of "
                + ", ".join(SCOPE_PRIORITIES)
            )
        entries.append(
            {
                "title": string(data["title"], "answers.scope.title"),
                "body": _text(data["body"], "answers.scope.body"),
                "priority": priority,
            }
        )
    return entries


def _wont(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        refuse("answers.wont must be a list")
    entries: list[dict[str, str]] = []
    for entry in raw:
        data = mapping(entry, "answers.wont entry")
        no_unknown(data, ("title", "reason"), "answers.wont entry")
        require(data, ("title", "reason"), "answers.wont entry")
        entries.append(
            {
                "title": string(data["title"], "answers.wont.title"),
                "reason": _text(data["reason"], "answers.wont.reason"),
            }
        )
    return entries


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        refuse(f"{what} must be a string")
    return value


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        refuse(f"{what} must be a list")
    return value
