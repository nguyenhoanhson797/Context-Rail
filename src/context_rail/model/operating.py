"""The optional ``operating`` block of a v2 pack: how the project is worked.

The block names only things the pack already declares (presets, workflows,
statuses, fields and actions). Every reference is checked here, so a pack that
cannot compile never reaches a command: an unknown key, preset, status, field
or action is refused instead of guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from context_rail.model.fields import (
    boolean,
    integer,
    mapping,
    no_unknown,
    refuse,
    string,
    string_list,
)
from context_rail.model.primitives import CATEGORIES

if TYPE_CHECKING:
    from context_rail.model.pack import Pack

__all__ = [
    "COMPLETE_KEYS",
    "EXECUTION_KEYS",
    "FOCUS_KEYS",
    "FOCUS_MODES",
    "FOCUS_SCOPES",
    "HIERARCHY_KEYS",
    "OPERATING_KEYS",
    "OPERATING_VERSION",
    "ORDER_KEYS",
    "PROGRESS_KEYS",
    "QUEUE_KEYS",
    "QUEUE_REQUIREMENTS",
    "CompleteRule",
    "ExecutionPolicy",
    "FocusPolicy",
    "HierarchyLevel",
    "OperatingSpec",
    "ProgressPolicy",
    "QueuePolicy",
    "parse_operating",
]

OPERATING_VERSION = 1
OPERATING_KEYS = (
    "version",
    "unit",
    "hierarchy",
    "focus",
    "queue",
    "progress",
    "execution",
)
HIERARCHY_KEYS = ("preset", "order", "required")
FOCUS_KEYS = ("preset", "category", "order", "resolve")
QUEUE_KEYS = ("presets", "require", "focus")
PROGRESS_KEYS = ("group", "of", "complete")
COMPLETE_KEYS = ("states", "category", "presets")
EXECUTION_KEYS = ("actions",)
FOCUS_MODES = ("unique", "first", "explicit", "none")
QUEUE_REQUIREMENTS = ("transition", "action")
FOCUS_SCOPES = ("priority", "only")
#: Ordering keys that need no declaration because the engine already knows them.
ORDER_KEYS = ("sequence", "created", "updated", "title", "slug")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class HierarchyLevel:
    """One container level of the project, outermost first."""

    preset: str
    order: str | None = None
    required: bool = False


@dataclass(frozen=True)
class FocusPolicy:
    """Which container the project is working in, and how ambiguity resolves."""

    preset: str
    category: str
    order: str | None = None
    resolve: str = "unique"


@dataclass(frozen=True)
class QueuePolicy:
    """Which presets ``next`` returns and what makes one eligible."""

    presets: tuple[str, ...]
    require: str = "transition"
    focus: str = "priority"


@dataclass(frozen=True)
class CompleteRule:
    """What counts as complete: explicit states, a category, or per preset."""

    states: tuple[str, ...] = ()
    category: str | None = None
    presets: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgressPolicy:
    """The reporting population, its group and its completion rule."""

    group: str | None
    of: tuple[str, ...]
    complete: CompleteRule


@dataclass(frozen=True)
class ExecutionPolicy:
    """Declared actions whose runs the operating model treats as progress."""

    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatingSpec:
    """A validated ``operating`` block, exactly as the pack declared it."""

    version: int
    unit: str | None
    hierarchy: tuple[HierarchyLevel, ...]
    focus: FocusPolicy | None
    queue: QueuePolicy | None
    progress: ProgressPolicy | None
    execution: ExecutionPolicy


def parse_operating(raw: Any, pack: Pack) -> OperatingSpec:
    """Parse and validate one ``operating`` block against its own pack."""
    data = mapping(raw, "operating")
    no_unknown(data, OPERATING_KEYS, "operating")
    if "version" not in data:
        refuse("operating is missing version")
    version = integer(data["version"], "operating.version", positive=True)
    if version != OPERATING_VERSION:
        refuse(
            f"operating version {version} is not supported; "
            f"expected {OPERATING_VERSION}"
        )

    hierarchy = _hierarchy(data.get("hierarchy", []), pack)
    unit = _unit(data.get("unit"), pack)
    focus = _focus(data.get("focus"), pack, hierarchy)
    queue = _queue(data.get("queue"), pack)
    progress = _progress(data.get("progress"), pack, unit, hierarchy, queue)
    execution = _execution(data.get("execution"), pack)
    return OperatingSpec(
        version=version,
        unit=unit,
        hierarchy=hierarchy,
        focus=focus,
        queue=queue,
        progress=progress,
        execution=execution,
    )


def _unit(raw: Any, pack: Pack) -> str | None:
    if raw is None:
        return None
    name = string(raw, "operating.unit")
    preset = pack.presets.get(name)
    if preset is None:
        refuse(f"operating.unit names unknown preset {name!r}")
    if preset.primitive != "item":
        refuse(f"operating.unit preset {name!r} is not an item preset")
    return name


def _hierarchy(raw: Any, pack: Pack) -> tuple[HierarchyLevel, ...]:
    if not isinstance(raw, list):
        refuse("operating.hierarchy must be a list of levels")
    levels: list[HierarchyLevel] = []
    seen: set[str] = set()
    for position, entry in enumerate(raw):
        where = f"operating.hierarchy[{position}]"
        body = mapping(entry, where)
        no_unknown(body, HIERARCHY_KEYS, where)
        if "preset" not in body:
            refuse(f"{where} is missing preset")
        name = string(body["preset"], f"{where}.preset")
        preset = pack.presets.get(name)
        if preset is None:
            refuse(f"{where} names unknown preset {name!r}")
        if preset.primitive != "item" or preset.container is not True:
            refuse(f"{where} preset {name!r} is not a container")
        if name in seen:
            refuse(f"operating.hierarchy repeats preset {name!r}")
        seen.add(name)
        order = _order(body.get("order"), preset, f"{where}.order")
        required = boolean(body.get("required", False), f"{where}.required")
        levels.append(HierarchyLevel(preset=name, order=order, required=required))
    return tuple(levels)


def _focus(
    raw: Any, pack: Pack, hierarchy: tuple[HierarchyLevel, ...]
) -> FocusPolicy | None:
    if raw is None:
        return None
    body = mapping(raw, "operating.focus")
    no_unknown(body, FOCUS_KEYS, "operating.focus")
    for key in ("preset", "category"):
        if key not in body:
            refuse(f"operating.focus is missing {key}")
    name = string(body["preset"], "operating.focus.preset")
    if name not in {level.preset for level in hierarchy}:
        refuse(f"operating.focus preset {name!r} is not a hierarchy level")
    category = string(body["category"], "operating.focus.category")
    if category not in CATEGORIES:
        refuse(f"operating.focus category {category!r} is not a declared category")
    order = _order(body.get("order"), pack.presets[name], "operating.focus.order")
    resolve = string(body.get("resolve", "unique"), "operating.focus.resolve")
    if resolve not in FOCUS_MODES:
        refuse(
            "operating.focus.resolve must be one of " + ", ".join(FOCUS_MODES)
        )
    return FocusPolicy(
        preset=name, category=category, order=order, resolve=resolve
    )


def _queue(raw: Any, pack: Pack) -> QueuePolicy | None:
    if raw is None:
        return None
    body = mapping(raw, "operating.queue")
    no_unknown(body, QUEUE_KEYS, "operating.queue")
    if "presets" not in body:
        refuse("operating.queue is missing presets")
    presets = _item_presets(body["presets"], pack, "operating.queue.presets")
    require = string(body.get("require", "transition"), "operating.queue.require")
    if require not in QUEUE_REQUIREMENTS:
        refuse(
            "operating.queue.require must be one of " + ", ".join(QUEUE_REQUIREMENTS)
        )
    focus = string(body.get("focus", "priority"), "operating.queue.focus")
    if focus not in FOCUS_SCOPES:
        refuse("operating.queue.focus must be one of " + ", ".join(FOCUS_SCOPES))
    return QueuePolicy(presets=presets, require=require, focus=focus)


def _progress(
    raw: Any,
    pack: Pack,
    unit: str | None,
    hierarchy: tuple[HierarchyLevel, ...],
    queue: QueuePolicy | None,
) -> ProgressPolicy | None:
    if raw is None:
        return None
    body = mapping(raw, "operating.progress")
    no_unknown(body, PROGRESS_KEYS, "operating.progress")
    group = body.get("group")
    if group is not None:
        group = string(group, "operating.progress.group")
        if group not in {level.preset for level in hierarchy}:
            refuse(
                f"operating.progress.group {group!r} is not a hierarchy level"
            )
    if "of" in body:
        of = _item_presets(body["of"], pack, "operating.progress.of")
    elif unit is not None:
        of = (unit,)
    elif queue is not None:
        of = queue.presets
    else:
        of = ()
    complete = _complete(body.get("complete", {}), pack, of, "operating.progress")
    return ProgressPolicy(group=group, of=of, complete=complete)


def _complete(
    raw: Any, pack: Pack, of: tuple[str, ...], where: str
) -> CompleteRule:
    body = mapping(raw, f"{where}.complete")
    no_unknown(body, COMPLETE_KEYS, f"{where}.complete")
    states: tuple[str, ...] = ()
    if "states" in body:
        states = string_list(body["states"], f"{where}.complete.states")
        _check_states(pack, of, states, f"{where}.complete.states")
    category = body.get("category")
    if category is not None:
        category = string(category, f"{where}.complete.category")
        if category != "done":
            refuse(
                f"{where}.complete.category must be 'done', not {category!r}"
            )
    per_preset: dict[str, tuple[str, ...]] = {}
    if "presets" in body:
        declared = mapping(body["presets"], f"{where}.complete.presets")
        for name, raw_states in declared.items():
            if name not in of:
                refuse(
                    f"{where}.complete.presets names {name!r}, "
                    "which is outside operating.progress.of"
                )
            values = string_list(raw_states, f"{where}.complete.presets.{name}")
            _check_states(pack, (name,), values, f"{where}.complete.presets.{name}")
            per_preset[name] = values
    if not states and category is None and not per_preset:
        category = "done"
    return CompleteRule(states=states, category=category, presets=per_preset)


def _check_states(
    pack: Pack, presets: tuple[str, ...], states: tuple[str, ...], where: str
) -> None:
    """Every state must exist in every referenced workflow and be ``done``."""
    for preset_name in presets:
        workflow = pack.workflow_for(preset_name)
        for state in states:
            if state not in workflow.transitions:
                refuse(
                    f"{where} state {state!r} is not part of the "
                    f"{workflow.name!r} workflow"
                )
            if workflow.statuses.get(state) != "done":
                refuse(
                    f"{where} state {state!r} is not a done-category state "
                    f"of the {workflow.name!r} workflow"
                )


def _execution(raw: Any, pack: Pack) -> ExecutionPolicy:
    if raw is None:
        return ExecutionPolicy()
    body = mapping(raw, "operating.execution")
    no_unknown(body, EXECUTION_KEYS, "operating.execution")
    if "actions" not in body:
        return ExecutionPolicy()
    actions = string_list(
        body["actions"], "operating.execution.actions", allow_empty=True
    )
    unknown = [name for name in actions if name not in pack.actions]
    if unknown:
        refuse(
            "operating.execution.actions names unknown action(s): "
            + ", ".join(unknown)
        )
    return ExecutionPolicy(actions=actions)


def _item_presets(raw: Any, pack: Pack, where: str) -> tuple[str, ...]:
    names = string_list(raw, where)
    for name in names:
        preset = pack.presets.get(name)
        if preset is None:
            refuse(f"{where} names unknown preset {name!r}")
        if preset.primitive != "item":
            refuse(f"{where} preset {name!r} is not an item preset")
    return names


def _order(raw: Any, preset: Any, where: str) -> str | None:
    if raw is None:
        return None
    name = string(raw, where)
    if not _NAME.fullmatch(name):
        refuse(f"{where} must be a field name")
    if name in ORDER_KEYS or name in preset.fields:
        return name
    refuse(f"{where} names field {name!r}, which {preset.name!r} does not declare")
