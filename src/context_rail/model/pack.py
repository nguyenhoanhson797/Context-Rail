"""The pack (``pack.yaml``): statuses, transitions, presets and policy.

A pack may also ship ``layout.yaml`` with placement defaults for a new store.
Everything here fails closed: an unknown version, key, category, status or
policy value is refused instead of being guessed at.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn

import yaml

from context_rail.core.defaults import DEFAULT_PACK
from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.cache import cached_model
from context_rail.model.actions import Action, parse_actions
from context_rail.model.codegraph import PACK_CODE_GRAPH_CHOICES
from context_rail.model.errors import ModelError
from context_rail.model.fields import relative_path, reserved_fields
from context_rail.model.operating import OperatingSpec, parse_operating
from context_rail.model.primitives import (
    CATEGORIES,
    FINISHED_CATEGORIES,
    PACK_POLICY_KEYS,
    PRIMITIVES,
    WORKABLE_CATEGORIES,
)

__all__ = [
    "DEPRECATED_PACK_ALIASES",
    "DEFAULT_PACK",
    "LAYOUT_FILE",
    "LAYOUT_KEYS",
    "PACK_KEYS",
    "PACK_VERSION",
    "SUPPORTED_PACK_VERSIONS",
    "Pack",
    "Preset",
    "QualityRule",
    "Workflow",
    "ModelError",
    "REQUIRED_PRESETS",
    "canonical_pack_name",
    "check_pack_name",
    "effective_measurement_min_runs",
    "load_pack",
    "pack_layout",
    "pack_layout_source",
    "pack_policy_digest", "pack_source",
]

PACK_KEYS = (
    "name",
    "version",
    "statuses",
    "initial",
    "transitions",
    "presets",
    "policy",
    "execution",
    "workflows",
    "actions",
    "operating",
    "extends",
)
REQUIRED_PACK_KEYS = ("name", "version", "statuses", "initial", "transitions", "presets", "policy")
PRESET_KEYS = (
    "primitive",
    "tags",
    "fields",
    "template",
    "required",
    "defaults",
    "workflow",
    "container",
)
EXECUTION_KEYS = ("dispatch", "merge")
WORKFLOW_KEYS = ("initial", "transitions", "execution", "quality")
QUALITY_KEYS = ("gate", "checks")
LAYOUT_FILE = "layout.yaml"
LAYOUT_KEYS = ("placement", "presets")
PACK_VERSION = 2
SUPPORTED_PACK_VERSIONS = (1, 2)
DEFAULT_TEMPLATE = "templates/{primitive}.md"
DEFAULT_MEASUREMENT_MIN_RUNS = 10
_PACK_NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_WORKFLOW_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_GATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# The engine reads these presets by name (the charter and skill documents, the
# manifest's placed presets and the legacy migration fallback), so every pack
# must declare them rather than letting a store fail later.
REQUIRED_PRESETS = (
    "charter",
    "cto",
    "decision",
    "goal",
    "note",
    "profile",
    "scope",
    "skill",
    "vision",
    "wont",
)

# Names shipped by an earlier release. They keep working, and the store's
# manifest keeps the name it was approved with, so an approval still matches.
DEPRECATED_PACK_ALIASES = {"small": "basic", "indie": "flow"}


@dataclass(frozen=True)
class QualityRule:
    """The gate a workflow evaluates on entering one status, and its checks."""

    gate: str
    checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workflow:
    """One named lifecycle: an initial status, its moves and its quality bar."""

    name: str
    initial: str
    transitions: dict[str, tuple[str, ...]]
    execution: dict[str, dict[str, bool]]
    quality: dict[str, QualityRule]
    statuses: dict[str, str] = field(default_factory=dict)

    def dispatchable(self, status: str) -> bool:
        """Whether an Item in ``status`` may be dispatched under this workflow."""
        declared = self.execution.get(status, {})
        if "dispatch" in declared:
            return declared["dispatch"]
        return self.statuses.get(status) in WORKABLE_CATEGORIES

    def mergeable(self, status: str) -> bool:
        """Whether an Item in ``status`` may be merged under this workflow."""
        declared = self.execution.get(status, {})
        if "merge" in declared:
            return declared["merge"]
        return self.statuses.get(status) not in FINISHED_CATEGORIES

    def gate_for(self, target: str) -> str | None:
        """The gate this workflow evaluates on entering ``target``, if any."""
        rule = self.quality.get(target)
        return None if rule is None else rule.gate

    def checks_for(self, target: str) -> tuple[str, ...] | None:
        """The checks this workflow selects for ``target``, or None for all."""
        rule = self.quality.get(target)
        if rule is None or not rule.checks:
            return None
        return rule.checks


@dataclass(frozen=True)
class Preset:
    """One preset: a primitive, its tags, enum fields and body template."""

    name: str
    primitive: str
    tags: tuple[str, ...]
    fields: dict[str, tuple[str, ...]]
    template: str
    required: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    workflow: str = "default"
    container: bool | None = None


@dataclass(frozen=True)
class Pack:
    """A validated ``pack.yaml``."""

    name: str
    version: int
    statuses: dict[str, str]
    initial: str
    transitions: dict[str, tuple[str, ...]]
    presets: dict[str, Preset]
    policy: dict[str, Any]
    execution: dict[str, dict[str, bool]]
    workflows: dict[str, Workflow]
    actions: dict[str, Action] = field(default_factory=dict)
    operating: OperatingSpec | None = None
    extends: str | None = None

    def workflow(self, name: str) -> Workflow:
        """The workflow called ``name``, refusing an unknown one."""
        workflow = self.workflows.get(name)
        if workflow is None:
            _refuse(f"unknown workflow {name!r}")
        return workflow

    def workflow_for(self, preset: str) -> Workflow:
        """The workflow a preset selects, refusing an unknown preset."""
        declared = self.presets.get(preset)
        if declared is None:
            _refuse(f"unknown preset {preset!r}")
        return self.workflow(declared.workflow)

    def action(self, name: str) -> Action:
        """The action called ``name``, refusing an unknown verb."""
        action = self.actions.get(name)
        if action is None:
            _refuse(f"unknown action {name!r}")
        return action


def pack_source(name: str, store: Path) -> Any | None:
    """The resolved ``pack.yaml`` source for ``name`` in ``store`` (local, then built-in)."""
    _check_name(name)
    local = Path(store) / "packs" / name / "pack.yaml"
    if local.is_file():
        return local
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical / "pack.yaml"
    if local.is_file():
        return local
    builtin = _builtin_pack(canonical)
    return builtin


def load_pack(name: str, store: Path) -> Pack:
    """Load ``<store>/packs/<name>/pack.yaml``, else the built-in pack of that name.

    A deprecated alias resolves to the pack that replaced it, but the returned
    :class:`Pack` keeps the requested name: approvals bind to the manifest's
    pack name, which an existing store still spells the old way.

    When a pack declares ``extends: <base>``, the base pack is loaded and
    merged underneath it before validation: statuses and workflows are united
    (a base status re-mapped to a different category is refused, never
    guessed), presets and actions override by name, and ``operating`` is
    replaced when the child declares one. The merge stays fail-closed: an
    unknown ``extends``, a cycle, or a child preset naming an unknown
    workflow or status is refused at load time.
    """
    return _load_pack(name, store, seen=())


def _load_pack(name: str, store: Path, *, seen: tuple[str, ...]) -> Pack:
    _check_name(name)
    if name in seen:
        _refuse("pack extends cycle: " + " -> ".join([*seen, name]))
    local = Path(store) / "packs" / name / "pack.yaml"
    if local.is_file():
        pack = cached_model(local, lambda: _parse_pack(_read_text(local), name))
        return _resolve_extends(pack, store, seen=( *seen, name))
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical / "pack.yaml"
    if local.is_file():
        pack = cached_model(local, lambda: _parse_pack(_read_text(local), canonical))
        resolved = _resolve_extends(pack, store, seen=( *seen, name))
        return replace(resolved, name=name)
    builtin = _builtin_pack(canonical)
    if builtin is None:
        _refuse(f"unknown pack {name!r}")
    pack = cached_model(builtin, lambda: _parse_pack(_read_text(builtin), canonical), packaged=True)
    resolved = _resolve_extends(pack, store, seen=( *seen, name))
    return replace(resolved, name=name)


def _resolve_extends(pack: Pack, store: Path, *, seen: tuple[str, ...]) -> Pack:
    """Merge ``pack.extends`` underneath ``pack`` (every layer validated)."""
    if pack.extends is None:
        return pack
    extends = pack.extends
    if not isinstance(extends, str) or not extends.strip():
        _refuse(f"pack {pack.name!r} extends must be a pack name")
    if extends == pack.name:
        _refuse(f"pack {pack.name!r} cannot extend itself")
    base = _load_pack(extends, store, seen=seen)
    merged_statuses = dict(base.statuses)
    for status, category in pack.statuses.items():
        existing = merged_statuses.get(status)
        if existing is not None and existing != category:
            _refuse(f"pack {pack.name!r} remaps status {status!r} from {existing!r} to {category!r}")
        merged_statuses[status] = category
    merged_workflows = dict(base.workflows)
    for workflow_name, workflow in pack.workflows.items():
        if workflow_name == "default":
            continue
        merged_workflows[workflow_name] = workflow
    merged_presets = dict(base.presets)
    merged_presets.update(pack.presets)
    merged_actions = dict(base.actions)
    merged_actions.update(pack.actions)
    merged_policy = dict(base.policy)
    merged_policy.update(pack.policy)
    # Every child preset's workflow and every child action source must resolve
    # against the merged registries (child presets may select base workflows).
    for preset in merged_presets.values():
        if preset.workflow not in merged_workflows:
            _refuse(f"pack {pack.name!r} preset {preset.name!r} names unknown workflow {preset.workflow!r}")
    return replace(
        pack,
        statuses=merged_statuses,
        workflows=merged_workflows,
        presets=merged_presets,
        actions=merged_actions,
        policy=merged_policy,
    )


def pack_layout_source(name: str, store: Path) -> Any | None:
    """The optional ``layout.yaml`` beside the pack, or None when it ships none."""
    _check_name(name)
    local = Path(store) / "packs" / name
    if (local / "pack.yaml").is_file():
        layout = local / LAYOUT_FILE
        return layout if layout.is_file() else None
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical
    if (local / "pack.yaml").is_file():
        layout = local / LAYOUT_FILE
        return layout if layout.is_file() else None
    builtin = _builtin_pack(canonical)
    if builtin is None:
        return None
    layout = builtin.parent.joinpath(LAYOUT_FILE)
    return layout if layout.is_file() else None


def pack_layout(name: str, store: Path) -> dict[str, Any] | None:
    """Load a pack's layout defaults, refusing an unknown key or malformed file."""
    source = pack_layout_source(name, store)
    if source is None:
        return None
    data = _mapping(
        _strict_yaml(_read_text(source, LAYOUT_FILE), LAYOUT_FILE), LAYOUT_FILE
    )
    unknown = sorted(set(data) - set(LAYOUT_KEYS))
    if unknown:
        _refuse(f"{LAYOUT_FILE} has unknown key(s): " + ", ".join(unknown))
    placement = _mapping(data.get("placement", {}), f"{LAYOUT_FILE} placement")
    presets = _mapping(data.get("presets", {}), f"{LAYOUT_FILE} presets")
    return {"placement": dict(placement), "presets": dict(presets)}


def canonical_pack_name(name: str) -> str:
    """The current name of ``name``: a deprecated alias resolves to its successor."""
    return DEPRECATED_PACK_ALIASES.get(name, name)


def check_pack_name(name: str) -> None:
    """Refuse a name that cannot safely become a directory under ``packs/``."""
    _check_name(name)


def effective_measurement_min_runs(pack: Pack) -> int:
    """The pack's minimum comparable runs, or the fixed default when it is absent."""
    value = pack.policy.get("measurement_min_runs", DEFAULT_MEASUREMENT_MIN_RUNS)
    return int(value)


def pack_policy_digest(pack: Pack) -> str:
    """The digest an approval of this pack's policy binds to."""
    canonical = json.dumps(
        pack.policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256_hex(canonical.encode("utf-8"))


def _builtin_pack(name: str) -> Any | None:
    candidate = resources.files("context_rail").joinpath("packs", name, "pack.yaml")
    return candidate if candidate.is_file() else None


def _parse_pack(text: str, requested_name: str) -> Pack:
    data = _mapping(_strict_yaml(text), "pack.yaml")

    if "version" not in data:
        _refuse("pack.yaml is missing version")
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        _refuse("pack version must be an integer")
    if version not in SUPPORTED_PACK_VERSIONS:
        raise ModelError(
            "SCHEMA_UNSUPPORTED",
            f"pack version {version} is not supported; "
            f"expected {PACK_VERSION}",
        )

    unknown = sorted(set(data) - set(PACK_KEYS))
    if unknown:
        _refuse("unknown pack key(s): " + ", ".join(unknown))
    missing = [key for key in REQUIRED_PACK_KEYS if key not in data]
    if missing:
        _refuse("pack.yaml is missing " + ", ".join(missing))

    name = data["name"]
    if not isinstance(name, str) or not name:
        _refuse("pack name must be a non-empty string")
    if name != requested_name:
        _refuse(f"pack.yaml is named {name!r} but {requested_name!r} was requested")

    extends_raw = data.get("extends")
    extends: str | None = None
    if extends_raw is not None:
        if not isinstance(extends_raw, str) or not extends_raw.strip():
            _refuse("extends must be a pack name")
        extends = extends_raw.strip()
        if not _PACK_NAME.fullmatch(extends):
            _refuse(f"invalid pack name {extends!r}")

    statuses = _statuses(data["statuses"])
    initial = data["initial"]
    if not isinstance(initial, str) or initial not in statuses:
        _refuse(f"initial status {initial!r} is not a declared status")
    transitions = _transitions(data["transitions"], statuses)
    execution = _execution(data.get("execution", {}), statuses)
    workflows = _workflows(
        data.get("workflows", {}), statuses, initial, transitions, execution
    )
    presets = _presets(data["presets"], workflows)
    policy = _policy(data["policy"])
    actions = parse_actions(data.get("actions", {}), statuses, presets, workflows)

    pack = Pack(
        name=name,
        version=version,
        statuses=statuses,
        initial=initial,
        transitions=transitions,
        presets=presets,
        policy=policy,
        execution=execution,
        workflows=workflows,
        actions=actions,
        extends=extends,
    )
    if "operating" not in data:
        return pack
    if version < 2:
        _refuse("pack version 1 may not declare operating; declare version 2")
    return replace(pack, operating=parse_operating(data["operating"], pack))


def _statuses(raw: Any) -> dict[str, str]:
    statuses = _mapping(raw, "statuses")
    if not statuses:
        _refuse("statuses must declare at least one status")
    for status, category in statuses.items():
        if not isinstance(category, str):
            _refuse(f"status {status!r} must map to a category name")
        if category not in CATEGORIES:
            _refuse(f"status {status!r} maps to unknown category {category!r}")
    return dict(statuses)


def _transitions(raw: Any, statuses: dict[str, str]) -> dict[str, tuple[str, ...]]:
    transitions = _mapping(raw, "transitions")
    result: dict[str, tuple[str, ...]] = {}
    for source, targets in transitions.items():
        if source not in statuses:
            _refuse(f"transitions from undeclared status {source!r}")
        if not isinstance(targets, list):
            _refuse(f"transitions for {source!r} must be a list of statuses")
        values: list[str] = []
        for target in targets:
            if not isinstance(target, str) or target not in statuses:
                _refuse(
                    f"transitions.{source} targets unknown status {target!r}"
                )
            values.append(target)
        result[source] = tuple(values)
    return result


def _presets(raw: Any, workflows: dict[str, Workflow]) -> dict[str, Preset]:
    presets = _mapping(raw, "presets")
    return {
        name: _preset(name, entry, workflows) for name, entry in presets.items()
    }


def _workflows(
    raw: Any,
    statuses: dict[str, str],
    initial: str,
    transitions: dict[str, tuple[str, ...]],
    execution: dict[str, dict[str, bool]],
) -> dict[str, Workflow]:
    """The default workflow from the top level, then every declared workflow."""
    workflows = {
        "default": Workflow(
            name="default",
            initial=initial,
            transitions=transitions,
            execution=execution,
            quality={},
            statuses=statuses,
        )
    }
    declared = _mapping(raw, "workflows")
    for name, entry in declared.items():
        if name == "default":
            _refuse("workflows may not declare 'default'")
        if not _WORKFLOW_NAME.fullmatch(name):
            _refuse(f"invalid workflow name {name!r}")
        where = f"workflows.{name}"
        body = _mapping(entry, where)
        unknown = sorted(set(body) - set(WORKFLOW_KEYS))
        if unknown:
            _refuse(f"{where} has unknown key(s): " + ", ".join(unknown))
        missing = [key for key in ("initial", "transitions") if key not in body]
        if missing:
            _refuse(f"{where} is missing " + ", ".join(missing))
        workflow_initial = body["initial"]
        if not isinstance(workflow_initial, str) or workflow_initial not in statuses:
            _refuse(f"{where} initial status {workflow_initial!r} is not declared")
        workflow_transitions = _transitions(body["transitions"], statuses)
        workflow_execution = _execution(body.get("execution", {}), statuses)
        workflow_quality = _quality(body.get("quality", {}), statuses)
        workflows[name] = Workflow(
            name=name,
            initial=workflow_initial,
            transitions=workflow_transitions,
            execution=workflow_execution,
            quality=workflow_quality,
            statuses=statuses,
        )
    return workflows


def _quality(raw: Any, statuses: dict[str, str]) -> dict[str, QualityRule]:
    quality = _mapping(raw, "quality")
    result: dict[str, QualityRule] = {}
    for status, raw_rule in quality.items():
        if status not in statuses:
            _refuse(f"quality names undeclared status {status!r}")
        where = f"quality.{status}"
        rule = _mapping(raw_rule, where)
        unknown = sorted(set(rule) - set(QUALITY_KEYS))
        if unknown:
            _refuse(f"{where} has unknown key(s): " + ", ".join(unknown))
        gate = rule.get("gate")
        if not isinstance(gate, str) or not _GATE_NAME.fullmatch(gate):
            _refuse(f"{where} gate must be a safe gate name")
        checks = rule.get("checks", [])
        if not isinstance(checks, list) or not all(
            isinstance(check, str) and check for check in checks
        ):
            _refuse(f"{where} checks must be a list of non-empty names")
        result[status] = QualityRule(gate=gate, checks=tuple(checks))
    return result


def _execution(raw: Any, statuses: dict[str, str]) -> dict[str, dict[str, bool]]:
    execution = _mapping(raw, "execution")
    result: dict[str, dict[str, bool]] = {}
    for status, raw_flags in execution.items():
        if status not in statuses:
            _refuse(f"execution names undeclared status {status!r}")
        flags = _mapping(raw_flags, f"execution.{status}")
        unknown = sorted(set(flags) - set(EXECUTION_KEYS))
        if unknown:
            _refuse(f"execution.{status} has unknown key(s): " + ", ".join(unknown))
        values: dict[str, bool] = {}
        for key, value in flags.items():
            if not isinstance(value, bool):
                _refuse(f"execution.{status}.{key} must be true or false")
            values[key] = value
        result[status] = values
    return result


def _preset(
    name: str, raw: Any, workflows: dict[str, Workflow]
) -> Preset:
    entry = _mapping(raw, f"preset {name!r}")
    unknown = sorted(set(entry) - set(PRESET_KEYS))
    if unknown:
        _refuse(f"preset {name!r} has unknown key(s): " + ", ".join(unknown))
    if "primitive" not in entry:
        _refuse(f"preset {name!r} is missing primitive")
    primitive = entry["primitive"]
    if not isinstance(primitive, str) or primitive not in PRIMITIVES:
        _refuse(f"preset {name!r} has unknown primitive {primitive!r}")
    workflow = entry.get("workflow", "default")
    if not isinstance(workflow, str) or workflow not in workflows:
        _refuse(f"preset {name!r} names unknown workflow {workflow!r}")
    container = entry.get("container")
    if container is not None and not isinstance(container, bool):
        _refuse(f"preset {name!r} container must be true, false or null")
    if container is not None and primitive != "item":
        _refuse(f"preset {name!r} container is only valid on an item preset")
    tags = _string_list(entry.get("tags", []), f"preset {name!r} tags")
    fields = _fields(entry.get("fields", {}), name)
    required = _required(entry.get("required", []), name, primitive)
    defaults = _defaults(entry.get("defaults", {}), name, required, fields)
    template = entry.get("template", DEFAULT_TEMPLATE.format(primitive=primitive))
    if not isinstance(template, str) or not template:
        _refuse(f"preset {name!r} template must be a non-empty string")
    template = relative_path(template, f"preset {name!r} template")
    return Preset(
        name=name,
        primitive=primitive,
        tags=tags,
        fields=fields,
        template=template,
        required=required,
        defaults=defaults,
        workflow=workflow,
        container=container,
    )


def _required(raw: Any, preset: str, primitive: str) -> tuple[str, ...]:
    names = _string_list(raw, f"preset {preset!r} required")
    if len(set(names)) != len(names):
        _refuse(f"preset {preset!r} required repeats a field")
    reserved = reserved_fields(primitive)
    for name in names:
        if not _FIELD_NAME.fullmatch(name):
            _refuse(f"preset {preset!r} required has a malformed field {name!r}")
        if name in reserved:
            _refuse(f"preset {preset!r} required names reserved field {name!r}")
    return names


def _defaults(
    raw: Any,
    preset: str,
    required: tuple[str, ...],
    fields: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    defaults = _mapping(raw, f"preset {preset!r} defaults")
    missing = sorted(set(defaults) - set(required))
    if missing:
        _refuse(
            f"preset {preset!r} defaults field(s) that are not required: "
            + ", ".join(missing)
        )
    for name, value in defaults.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            if not value:
                _refuse(f"preset {preset!r} default {name!r} must not be empty")
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                _refuse(f"preset {preset!r} default {name!r} must be finite")
        else:
            _refuse(
                f"preset {preset!r} default {name!r} must be a JSON scalar"
            )
        allowed = fields.get(name)
        if allowed is not None and value not in allowed:
            _refuse(
                f"preset {preset!r} default {name!r} is outside its declared enum"
            )
    return dict(defaults)


def _fields(raw: Any, preset: str) -> dict[str, tuple[str, ...]]:
    fields = _mapping(raw, f"preset {preset!r} fields")
    return {
        name: _string_list(values, f"preset {preset!r} field {name!r}")
        for name, values in fields.items()
    }


def _policy(raw: Any) -> dict[str, Any]:
    policy = _mapping(raw, "policy")
    unknown = sorted(set(policy) - set(PACK_POLICY_KEYS) - {"code_graph"})
    if unknown:
        _refuse("unknown policy key(s): " + ", ".join(unknown))
    missing = [key for key in ("stuck_hours", "retries") if key not in policy]
    if missing:
        _refuse("pack.yaml policy is missing " + ", ".join(missing))

    stuck_hours = policy["stuck_hours"]
    if isinstance(stuck_hours, bool) or not isinstance(stuck_hours, (int, float)):
        _refuse("policy stuck_hours must be a number")
    if not math.isfinite(stuck_hours) or stuck_hours < 0:
        _refuse("policy stuck_hours must be finite and non-negative")

    retries = policy["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int):
        _refuse("policy retries must be a non-negative integer")
    if retries < 0:
        _refuse("policy retries must be a non-negative integer")

    result: dict[str, Any] = {"stuck_hours": stuck_hours, "retries": retries}
    if "measurement_min_runs" in policy:
        minimum = policy["measurement_min_runs"]
        if isinstance(minimum, bool) or not isinstance(minimum, int):
            _refuse("policy measurement_min_runs must be an integer")
        if minimum < DEFAULT_MEASUREMENT_MIN_RUNS:
            _refuse(
                "policy measurement_min_runs must be at least "
                f"{DEFAULT_MEASUREMENT_MIN_RUNS}"
            )
        result["measurement_min_runs"] = minimum
    if "code_graph" in policy:
        choice = policy["code_graph"]
        if choice not in PACK_CODE_GRAPH_CHOICES:
            _refuse(
                "policy code_graph must be one of: " + ", ".join(PACK_CODE_GRAPH_CHOICES)
            )
        result["code_graph"] = choice
    return result


def _string_list(raw: Any, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        _refuse(f"{what} must be a list of strings")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            _refuse(f"{what} must contain non-empty strings")
        values.append(value)
    return tuple(values)


def _check_name(name: Any) -> None:
    if not isinstance(name, str) or not name:
        _refuse("pack name must be a non-empty string")
    if not _PACK_NAME.fullmatch(name):
        _refuse(f"invalid pack name {name!r}")


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse(f"{what} must be a mapping")
    for key in value:
        if not isinstance(key, str) or not key:
            _refuse(f"{what} keys must be non-empty strings")
    return value


def _read_text(source: Any, label: str = "pack.yaml") -> str:
    try:
        return source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        _refuse(f"{label} is not valid UTF-8: {exc}")
    except OSError as exc:
        _refuse(f"cannot read {label}: {exc}")


_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _strict_yaml(text: str, label: str = "pack.yaml") -> Any:
    try:
        node = yaml.compose(text, Loader=_LOADER)
    except yaml.YAMLError:
        _refuse(f"{label} is not valid YAML")
    if node is not None:
        _reject_duplicate_keys(node, label)
    return load_yaml(text)


def _reject_duplicate_keys(node: Any, label: str = "pack.yaml") -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                marker = (key_node.tag, key_node.value)
                if marker in seen:
                    _refuse(f"{label} repeats the key {key_node.value!r}")
                seen.add(marker)
            _reject_duplicate_keys(value_node, label)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _reject_duplicate_keys(item, label)


def _refuse(message: str) -> NoReturn:
    raise ModelError("VALIDATION_FAILED", message)
