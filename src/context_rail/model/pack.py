"""The pack (``pack.yaml``): statuses, transitions, presets and policy.

Everything here fails closed: an unknown version, key, category, status or
policy value is refused instead of being guessed at.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn

import yaml

from context_rail.core.defaults import DEFAULT_PACK
from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.cache import cached_model
from context_rail.model.errors import ModelError
from context_rail.model.fields import relative_path
from context_rail.model.primitives import (
    CATEGORIES,
    PACK_POLICY_KEYS,
    PRIMITIVES,
)

__all__ = [
    "DEPRECATED_PACK_ALIASES",
    "DEFAULT_PACK",
    "PACK_KEYS",
    "PACK_VERSION",
    "Pack",
    "Preset",
    "ModelError",
    "REQUIRED_PRESETS",
    "canonical_pack_name",
    "check_pack_name",
    "effective_measurement_min_runs",
    "load_pack",
    "pack_policy_digest",
]

PACK_KEYS = ("name", "version", "statuses", "initial", "transitions", "presets", "policy")
PRESET_KEYS = ("primitive", "tags", "fields", "template")
PACK_VERSION = 1
DEFAULT_TEMPLATE = "templates/{primitive}.md"
DEFAULT_MEASUREMENT_MIN_RUNS = 10
_PACK_NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

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
class Preset:
    """One preset: a primitive, its tags, enum fields and body template."""

    name: str
    primitive: str
    tags: tuple[str, ...]
    fields: dict[str, tuple[str, ...]]
    template: str


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


def load_pack(name: str, store: Path) -> Pack:
    """Load ``<store>/packs/<name>/pack.yaml``, else the built-in pack of that name.

    A deprecated alias resolves to the pack that replaced it, but the returned
    :class:`Pack` keeps the requested name: approvals bind to the manifest's
    pack name, which an existing store still spells the old way.
    """
    _check_name(name)
    local = Path(store) / "packs" / name / "pack.yaml"
    if local.is_file():
        return cached_model(local, lambda: _parse_pack(_read_text(local), name))
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical / "pack.yaml"
    if local.is_file():
        pack = cached_model(local, lambda: _parse_pack(_read_text(local), canonical))
        return replace(pack, name=name)
    builtin = _builtin_pack(canonical)
    if builtin is None:
        _refuse(f"unknown pack {name!r}")
    pack = cached_model(builtin, lambda: _parse_pack(_read_text(builtin), canonical))
    return replace(pack, name=name)


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
    if version != PACK_VERSION:
        raise ModelError(
            "SCHEMA_UNSUPPORTED",
            f"pack version {version} is not supported; expected {PACK_VERSION}",
        )

    unknown = sorted(set(data) - set(PACK_KEYS))
    if unknown:
        _refuse("unknown pack key(s): " + ", ".join(unknown))
    missing = [key for key in PACK_KEYS if key not in data]
    if missing:
        _refuse("pack.yaml is missing " + ", ".join(missing))

    name = data["name"]
    if not isinstance(name, str) or not name:
        _refuse("pack name must be a non-empty string")
    if name != requested_name:
        _refuse(f"pack.yaml is named {name!r} but {requested_name!r} was requested")

    statuses = _statuses(data["statuses"])
    initial = data["initial"]
    if not isinstance(initial, str) or initial not in statuses:
        _refuse(f"initial status {initial!r} is not a declared status")
    transitions = _transitions(data["transitions"], statuses)
    presets = _presets(data["presets"])
    policy = _policy(data["policy"])

    return Pack(
        name=name,
        version=version,
        statuses=statuses,
        initial=initial,
        transitions=transitions,
        presets=presets,
        policy=policy,
    )


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


def _presets(raw: Any) -> dict[str, Preset]:
    presets = _mapping(raw, "presets")
    return {name: _preset(name, entry) for name, entry in presets.items()}


def _preset(name: str, raw: Any) -> Preset:
    entry = _mapping(raw, f"preset {name!r}")
    unknown = sorted(set(entry) - set(PRESET_KEYS))
    if unknown:
        _refuse(f"preset {name!r} has unknown key(s): " + ", ".join(unknown))
    if "primitive" not in entry:
        _refuse(f"preset {name!r} is missing primitive")
    primitive = entry["primitive"]
    if not isinstance(primitive, str) or primitive not in PRIMITIVES:
        _refuse(f"preset {name!r} has unknown primitive {primitive!r}")
    tags = _string_list(entry.get("tags", []), f"preset {name!r} tags")
    fields = _fields(entry.get("fields", {}), name)
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
    )


def _fields(raw: Any, preset: str) -> dict[str, tuple[str, ...]]:
    fields = _mapping(raw, f"preset {preset!r} fields")
    return {
        name: _string_list(values, f"preset {preset!r} field {name!r}")
        for name, values in fields.items()
    }


def _policy(raw: Any) -> dict[str, Any]:
    policy = _mapping(raw, "policy")
    unknown = sorted(set(policy) - set(PACK_POLICY_KEYS))
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


def _read_text(source: Any) -> str:
    try:
        return source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        _refuse(f"pack.yaml is not valid UTF-8: {exc}")
    except OSError as exc:
        _refuse(f"cannot read pack.yaml: {exc}")


_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _strict_yaml(text: str) -> Any:
    try:
        node = yaml.compose(text, Loader=_LOADER)
    except yaml.YAMLError:
        _refuse("pack.yaml is not valid YAML")
    if node is not None:
        _reject_duplicate_keys(node)
    return load_yaml(text)


def _reject_duplicate_keys(node: Any) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                marker = (key_node.tag, key_node.value)
                if marker in seen:
                    _refuse(f"pack.yaml repeats the key {key_node.value!r}")
                seen.add(marker)
            _reject_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _reject_duplicate_keys(item)


def _refuse(message: str) -> NoReturn:
    raise ModelError("VALIDATION_FAILED", message)
