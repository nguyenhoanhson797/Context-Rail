"""The pack (``pack.yaml``): statuses, transitions, presets and policy.

Everything here fails closed: an unknown version, key, category, status or
policy value is refused instead of being guessed at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn

from context_rail.core.util import load_yaml
from context_rail.model.cache import cached_model
from context_rail.model.errors import ModelError
from context_rail.model.primitives import CATEGORIES, PRIMITIVES

__all__ = [
    "PACK_KEYS",
    "PACK_VERSION",
    "POLICY_KEYS",
    "Pack",
    "Preset",
    "ModelError",
    "load_pack",
]

PACK_KEYS = ("name", "version", "statuses", "initial", "transitions", "presets", "policy")
PRESET_KEYS = ("primitive", "tags", "fields", "template")
POLICY_KEYS = ("stuck_hours", "retries")
PACK_VERSION = 1
DEFAULT_TEMPLATE = "templates/{primitive}.md"


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
    """Load ``<store>/packs/<name>/pack.yaml``, else the built-in pack of that name."""
    _check_name(name)
    local = Path(store) / "packs" / name / "pack.yaml"
    if local.is_file():
        return cached_model(local, lambda: _parse_pack(_read_text(local), name))
    builtin = _builtin_pack(name)
    if builtin is None:
        _refuse(f"unknown pack {name!r}")
    return cached_model(builtin, lambda: _parse_pack(_read_text(builtin), name))


def _builtin_pack(name: str) -> Any | None:
    candidate = resources.files("context_rail").joinpath("packs", name, "pack.yaml")
    return candidate if candidate.is_file() else None


def _parse_pack(text: str, requested_name: str) -> Pack:
    data = _mapping(load_yaml(text), "pack.yaml")

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
                _refuse(f"transition from {source!r} targets unknown status {target!r}")
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
    unknown = sorted(set(policy) - set(POLICY_KEYS))
    if unknown:
        _refuse("unknown policy key(s): " + ", ".join(unknown))
    missing = [key for key in POLICY_KEYS if key not in policy]
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

    return {"stuck_hours": stuck_hours, "retries": retries}


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
    if name in (".", "..") or any(char in name for char in ("/", "\\", ":", "\x00")):
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


def _refuse(message: str) -> NoReturn:
    raise ModelError("VALIDATION_FAILED", message)
