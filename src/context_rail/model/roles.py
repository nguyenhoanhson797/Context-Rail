"""Pack roles: what a seat may reach, do and see, built from fixed blocks.

A role is a name the pack chooses mapped to fixed keys (anchor, reach, can,
sees, workspace, independent_of, human, skills). Every reference is checked
against the pack here, so a role that cannot work never reaches a command: an
unknown key, preset, status, relation, action or role is refused, never guessed.
The engine reads these blocks and never branches on a role name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from context_rail.model.fields import (
    boolean,
    integer,
    mapping,
    no_unknown,
    refuse,
    require,
    string,
    string_list,
)
from context_rail.model.patterns import Pattern, parse_pattern
from context_rail.model.primitives import RELATIONS

if TYPE_CHECKING:
    from context_rail.model.pack import Pack, Workflow

__all__ = [
    "ANCHOR_KINDS",
    "CAN_KEYS",
    "CAN_TOOLS",
    "CODE_KEYS",
    "MAX_NAME_LENGTH",
    "REACH_KEYS",
    "READ_TERMS",
    "ROLE_KEYS",
    "SEES_KEYS",
    "SEES_PARTS",
    "STEP_KINDS",
    "WORKSPACES",
    "WRITE_TERMS",
    "Anchor",
    "Can",
    "ReachTerm",
    "Role",
    "Sees",
    "is_name",
    "parse_roles",
    "role_as_dict",
    "role_tools",
    "workflow_statuses",
]

ROLE_KEYS = (
    "anchor",
    "reach",
    "can",
    "sees",
    "workspace",
    "independent_of",
    "human",
    "skills",
)
REACH_KEYS = ("read", "write", "code")
CODE_KEYS = ("read", "write")
#: The ``can`` keys that map to one canonical write tool. ``grant`` has no tool.
CAN_TOOLS: dict[str, str] = {
    "add": "add",
    "update": "update",
    "move": "move",
    "link": "link",
    "rename": "rename",
    "relocate": "relocate",
    "check": "check",
    "verify": "verify",
    "dispatch": "dispatch",
    "review": "review",
    "merge": "merge",
    "report": "report",
    "triage": "triage",
    "execution": "execution record",
    "tick": "cto tick",
    "actions": "do",
}
CAN_KEYS = (*CAN_TOOLS, "grant")
SEES_KEYS = ("up", "down", "relations", "parts", "budget")
SEES_PARTS = (
    "title",
    "status",
    "next",
    "body",
    "acceptance",
    "blockers",
    "waits",
    "runs",
    "records",
    "evidence",
    "diff",
    "code_area",
)
WORKSPACES = ("none", "read", "write")
STEP_KINDS = ("produce", "review", "grant")
ANCHOR_KINDS = ("project", "level", "item")
READ_TERMS = ("project", "anchor", "up", "related", "preset", "path")
WRITE_TERMS = ("project", "anchor", "preset", "path")

#: Primitives a ``can`` key may name; every other key names item presets only.
_WRITABLE = {
    "add": ("item", "note", "record"),
    "update": ("item", "note"),
    "rename": ("item", "note"),
}
_FLAGS = ("relocate", "tick")
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_SKILL = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_NAME_LENGTH = 40
_MAX_LEVELS = 16
_BUDGET_KEYS = ("chars", "docs")
_DEFAULT_PARTS = ("title", "status", "body")
_DEFAULT_CHARS = 8000
_DEFAULT_DOCS = 40


@dataclass(frozen=True)
class Anchor:
    """Where a seat of the role sits: the project, one container or one item."""

    kind: str
    presets: tuple[str, ...]


@dataclass(frozen=True)
class ReachTerm:
    """One store-reach term; ``value`` depends on ``kind``."""

    kind: str
    value: Any


@dataclass(frozen=True)
class Can:
    """The write abilities a role holds, keyed by ``CAN_KEYS``."""

    methods: dict[str, Any]


@dataclass(frozen=True)
class Sees:
    """The context recipe for a seat brief. ``down`` None means all levels."""

    up: int
    down: int | None
    relations: tuple[str, ...]
    parts: tuple[str, ...]
    chars: int
    docs: int


@dataclass(frozen=True)
class Role:
    """One validated role, exactly as the pack declared it."""

    name: str
    anchor: Anchor
    read: tuple[ReachTerm, ...]
    write: tuple[ReachTerm, ...]
    code_read: tuple[Pattern, ...]
    code_write: tuple[Pattern, ...]
    can: Can
    sees: Sees
    workspace: str
    independent_of: tuple[str, ...]
    human: bool
    skills: tuple[str, ...]


def parse_roles(raw: Any, pack: Pack) -> dict[str, Role]:
    """Parse and validate one ``roles`` block against its own pack."""
    data = mapping(raw, "operating.roles")
    names = tuple(data)
    for name in names:
        if not is_name(name):
            refuse(
                f"operating.roles has an invalid role name {name!r}; "
                f"use a lowercase slug of at most {MAX_NAME_LENGTH} characters"
            )
    return {name: _role(name, data[name], pack, names) for name in names}


def is_name(value: str) -> bool:
    """Whether ``value`` is a valid role or seat name: a short lowercase slug."""
    return bool(_NAME.fullmatch(value)) and len(value) <= MAX_NAME_LENGTH


def workflow_statuses(workflow: Workflow) -> tuple[str, ...]:
    """Every status a workflow can be in, in the order the pack declares them."""
    seen = dict.fromkeys(workflow.transitions)
    for targets in workflow.transitions.values():
        seen.update(dict.fromkeys(targets))
    return tuple(seen)


def role_tools(role: Role) -> frozenset[str]:
    """The canonical write tools a role may call; ``grant`` is not a tool."""
    return frozenset(CAN_TOOLS[key] for key in role.can.methods if key in CAN_TOOLS)


def role_as_dict(role: Role) -> dict[str, Any]:
    """The role in the spelling a pack declares it, so ``parse_roles`` accepts it."""
    return {
        "anchor": _anchor_as_dict(role.anchor),
        "reach": {
            "read": [_term_as_dict(term) for term in role.read],
            "write": [_term_as_dict(term) for term in role.write],
            "code": {
                "read": [pattern.raw for pattern in role.code_read],
                "write": [pattern.raw for pattern in role.code_write],
            },
        },
        "can": {key: _can_as_value(value) for key, value in role.can.methods.items()},
        "sees": {
            "up": role.sees.up,
            "down": "all" if role.sees.down is None else role.sees.down,
            "relations": list(role.sees.relations),
            "parts": list(role.sees.parts),
            "budget": {"chars": role.sees.chars, "docs": role.sees.docs},
        },
        "workspace": role.workspace,
        "independent_of": list(role.independent_of),
        "human": role.human,
        "skills": list(role.skills),
    }


def _anchor_as_dict(anchor: Anchor) -> Any:
    if anchor.kind == "project":
        return "project"
    if anchor.kind == "level":
        return {"level": anchor.presets[0]}
    return {"item": list(anchor.presets)}


def _term_as_dict(term: ReachTerm) -> Any:
    if term.kind in ("project", "anchor"):
        return term.kind
    if term.kind == "up":
        return {"up": term.value}
    if term.kind == "path":
        return {"path": term.value.raw}
    return {term.kind: list(term.value)}


def _can_as_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {name: list(statuses) for name, statuses in value.items()}
    if isinstance(value, tuple):
        return list(value)
    return value


def _role(name: str, raw: Any, pack: Pack, names: tuple[str, ...]) -> Role:
    where = f"operating.roles.{name}"
    body = mapping(raw, where)
    no_unknown(body, ROLE_KEYS, where)
    require(body, ("anchor", "reach"), where)
    anchor = _anchor(body["anchor"], pack, f"{where}.anchor")
    read, write, code_read, code_write = _reach(
        body["reach"], pack, anchor, f"{where}.reach"
    )
    return Role(
        name=name,
        anchor=anchor,
        read=read,
        write=write,
        code_read=code_read,
        code_write=code_write,
        can=_can(body.get("can", {}), pack, names, f"{where}.can"),
        sees=_sees(body.get("sees", {}), f"{where}.sees"),
        workspace=_choice(
            body.get("workspace", "none"), WORKSPACES, f"{where}.workspace"
        ),
        independent_of=_choices(
            body.get("independent_of", []), STEP_KINDS, f"{where}.independent_of"
        ),
        human=boolean(body.get("human", False), f"{where}.human"),
        skills=_skills(body.get("skills", []), f"{where}.skills"),
    )


def _anchor(raw: Any, pack: Pack, where: str) -> Anchor:
    if raw == "project":
        return Anchor("project", ())
    if not isinstance(raw, dict):
        refuse(
            f"{where} must be 'project', {{level: <preset>}} or {{item: [<presets>]}}"
        )
    if len(raw) != 1:
        refuse(f"{where} must have exactly one of level, item")
    ((kind, value),) = raw.items()
    if kind == "level":
        name = string(value, f"{where}.level")
        preset = _preset(pack, name, f"{where}.level")
        if preset.primitive != "item" or preset.container is not True:
            refuse(f"{where}.level preset {name!r} is not a container")
        return Anchor("level", (name,))
    if kind == "item":
        names = string_list(value, f"{where}.item")
        for name in names:
            preset = _preset(pack, name, f"{where}.item")
            if preset.primitive != "item":
                refuse(f"{where}.item preset {name!r} is not an item preset")
            if preset.container is True:
                refuse(f"{where}.item preset {name!r} is a container; use level")
        return Anchor("item", names)
    refuse(f"{where} has unknown anchor kind {kind!r}")


def _reach(
    raw: Any, pack: Pack, anchor: Anchor, where: str
) -> tuple[
    tuple[ReachTerm, ...],
    tuple[ReachTerm, ...],
    tuple[Pattern, ...],
    tuple[Pattern, ...],
]:
    body = mapping(raw, where)
    no_unknown(body, REACH_KEYS, where)
    require(body, ("read",), where)
    code = mapping(body.get("code", {}), f"{where}.code")
    no_unknown(code, CODE_KEYS, f"{where}.code")
    return (
        _terms(body["read"], READ_TERMS, pack, anchor, f"{where}.read"),
        _terms(body.get("write", []), WRITE_TERMS, pack, anchor, f"{where}.write"),
        _patterns(code.get("read", []), pack, anchor, f"{where}.code.read"),
        _patterns(code.get("write", []), pack, anchor, f"{where}.code.write"),
    )


def _terms(
    raw: Any, allowed: tuple[str, ...], pack: Pack, anchor: Anchor, where: str
) -> tuple[ReachTerm, ...]:
    if not isinstance(raw, list):
        refuse(f"{where} must be a list of reach terms")
    terms: list[ReachTerm] = []
    for position, entry in enumerate(raw):
        spot = f"{where}[{position}]"
        if isinstance(entry, str) and entry in ("project", "anchor"):
            terms.append(ReachTerm(entry, None))
            continue
        if not isinstance(entry, dict):
            refuse(f"{spot} is an unknown reach term: {entry!r}")
        if len(entry) != 1:
            refuse(f"{spot} must have exactly one key")
        ((kind, value),) = entry.items()
        if kind not in READ_TERMS:
            refuse(f"{spot} is an unknown reach term: {kind!r}")
        if kind not in allowed:
            refuse(f"{spot} term {kind!r} is read-only")
        terms.append(
            ReachTerm(kind, _term_value(kind, value, pack, anchor, f"{spot}.{kind}"))
        )
    return tuple(terms)


def _term_value(kind: str, value: Any, pack: Pack, anchor: Anchor, where: str) -> Any:
    if kind == "up":
        return integer(value, where, positive=True, maximum=_MAX_LEVELS)
    if kind == "related":
        return _relations(value, where)
    if kind == "preset":
        names = string_list(value, where)
        for name in names:
            _preset(pack, name, where)
        return names
    return parse_pattern(
        value, where, presets=pack.presets, anchor_presets=anchor.presets
    )


def _patterns(raw: Any, pack: Pack, anchor: Anchor, where: str) -> tuple[Pattern, ...]:
    if not isinstance(raw, list):
        refuse(f"{where} must be a list of path patterns")
    return tuple(
        parse_pattern(
            entry,
            f"{where}[{position}]",
            presets=pack.presets,
            anchor_presets=anchor.presets,
        )
        for position, entry in enumerate(raw)
    )


def _can(raw: Any, pack: Pack, roles: tuple[str, ...], where: str) -> Can:
    body = mapping(raw, where)
    no_unknown(body, CAN_KEYS, where)
    methods: dict[str, Any] = {}
    for key, value in body.items():
        spot = f"{where}.{key}"
        if key == "move":
            methods[key] = _move(value, pack, spot)
        elif key == "link":
            methods[key] = _relations(value, spot)
        elif key == "actions":
            methods[key] = _actions(value, pack, spot)
        elif key == "grant":
            methods[key] = _grants(value, roles, spot)
        elif key in _FLAGS:
            if boolean(value, spot):
                methods[key] = True
        else:
            methods[key] = _presets(value, pack, _WRITABLE.get(key, ("item",)), spot)
    return Can(methods)


def _presets(
    raw: Any, pack: Pack, primitives: tuple[str, ...], where: str
) -> tuple[str, ...]:
    names = string_list(raw, where, allow_empty=True)
    for name in names:
        preset = _preset(pack, name, where)
        if preset.primitive in primitives:
            continue
        if primitives == ("item",):
            refuse(f"{where} preset {name!r} is not an item preset")
        refuse(
            f"{where} preset {name!r} ({preset.primitive}) cannot be written by a role"
        )
    return names


def _move(raw: Any, pack: Pack, where: str) -> dict[str, tuple[str, ...]]:
    body = mapping(raw, where)
    moves: dict[str, tuple[str, ...]] = {}
    for name, statuses in body.items():
        spot = f"{where}.{name}"
        preset = _preset(pack, name, spot)
        if preset.primitive != "item":
            refuse(f"{spot} preset {name!r} is not an item preset")
        wanted = string_list(statuses, spot, allow_empty=True)
        workflow = pack.workflow_for(name)
        reachable = workflow_statuses(workflow)
        for status in wanted:
            if status not in pack.statuses:
                refuse(f"{spot} names unknown status {status!r}")
            if status not in reachable:
                refuse(
                    f"{spot} status {status!r} is not part of the {workflow.name!r} workflow"
                )
        moves[name] = wanted
    return moves


def _relations(raw: Any, where: str) -> tuple[str, ...]:
    names = string_list(raw, where, allow_empty=True)
    for name in names:
        if name not in RELATIONS:
            refuse(f"{where} names unknown relation {name!r}")
    return names


def _actions(raw: Any, pack: Pack, where: str) -> tuple[str, ...]:
    names = string_list(raw, where, allow_empty=True)
    for name in names:
        if name not in pack.actions:
            refuse(f"{where} names unknown action {name!r}")
    return names


def _grants(raw: Any, roles: tuple[str, ...], where: str) -> tuple[str, ...]:
    names = string_list(raw, where, allow_empty=True)
    for name in names:
        if name not in roles:
            refuse(f"{where} names unknown role {name!r}")
    return names


def _sees(raw: Any, where: str) -> Sees:
    body = mapping(raw, where)
    no_unknown(body, SEES_KEYS, where)
    budget = mapping(body.get("budget", {}), f"{where}.budget")
    no_unknown(budget, _BUDGET_KEYS, f"{where}.budget")
    parts = string_list(
        body.get("parts", list(_DEFAULT_PARTS)), f"{where}.parts", allow_empty=True
    )
    for part in parts:
        if part not in SEES_PARTS:
            refuse(f"{where}.parts names unknown part {part!r}")
    return Sees(
        up=integer(body.get("up", 0), f"{where}.up", minimum=0, maximum=_MAX_LEVELS),
        down=_down(body.get("down", 0), f"{where}.down"),
        relations=_relations(body.get("relations", []), f"{where}.relations"),
        parts=parts,
        chars=integer(
            budget.get("chars", _DEFAULT_CHARS),
            f"{where}.budget.chars",
            minimum=500,
            maximum=64000,
        ),
        docs=integer(
            budget.get("docs", _DEFAULT_DOCS),
            f"{where}.budget.docs",
            minimum=1,
            maximum=200,
        ),
    )


def _down(raw: Any, where: str) -> int | None:
    if isinstance(raw, str):
        if raw != "all":
            refuse(f"{where} must be a number of levels or 'all', not {raw!r}")
        return None
    return integer(raw, where, minimum=0, maximum=_MAX_LEVELS)


def _choice(raw: Any, allowed: tuple[str, ...], where: str) -> str:
    value = string(raw, where)
    if value not in allowed:
        refuse(f"{where} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _choices(raw: Any, allowed: tuple[str, ...], where: str) -> tuple[str, ...]:
    return tuple(
        _choice(value, allowed, where)
        for value in string_list(raw, where, allow_empty=True)
    )


def _skills(raw: Any, where: str) -> tuple[str, ...]:
    skills = string_list(raw, where, allow_empty=True)
    for skill in skills:
        if not _SKILL.fullmatch(skill):
            refuse(f"{where} has an invalid skill slug {skill!r}")
    return skills


def _preset(pack: Pack, name: str, where: str) -> Any:
    preset = pack.presets.get(name)
    if preset is None:
        refuse(f"{where} names unknown preset {name!r}")
    return preset
