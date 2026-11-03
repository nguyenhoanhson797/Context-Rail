"""Preset metadata: defaults, required fields and the shared field checks.

Every creator calls :func:`prepare_fields` before it writes. The helper is pure
and raises :class:`ModelError`; handlers translate it at the boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from context_rail.model.errors import ModelError
from context_rail.model.fields import invalid_glob
from context_rail.model.pack import Preset

__all__ = [
    "ENGINE_PRESETS",
    "prepare_fields",
    "validate_engine_fields",
    "validate_preset_fields",
]

#: Presets the engine writes without an agent metadata surface. A required
#: field on one of these needs a default; `pack validate` reports the omission.
ENGINE_PRESETS = frozenset(
    {
        "event",
        "check",
        "fix",
        "note",
        "goal",
        "vision",
        "scope",
        "wont",
        "charter",
        "profile",
        "cto",
        "skill",
    }
)


def prepare_fields(preset: Preset, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Merge defaults, require metadata and validate one document's fields.

    ``fields`` is only the metadata the creator owns: a JSON record's ``data``
    or a markdown document's declared frontmatter. Engine stamps are never
    passed in, so they cannot be required or defaulted here.
    """
    if not isinstance(fields, Mapping):
        raise ModelError("VALIDATION_FAILED", "fields must be a mapping")
    values = dict(fields)
    for name, default in preset.defaults.items():
        values.setdefault(name, default)

    missing = [
        name
        for name in preset.required
        if name not in values or _empty(values[name])
    ]
    if missing:
        raise ModelError(
            "VALIDATION_FAILED",
            f"preset {preset.name!r} requires field(s): " + ", ".join(missing),
            {"missing": missing},
        )

    validate_preset_fields(preset, values)
    validate_engine_fields(values)
    return values


def validate_preset_fields(preset: Preset, fields: Mapping[str, Any]) -> None:
    """Refuse a declared field whose value is outside the preset's enum."""
    for key, allowed in preset.fields.items():
        if key not in fields:
            continue
        if fields[key] not in allowed:
            raise ModelError(
                "VALIDATION_FAILED",
                f"{key!r} must be one of: " + ", ".join(allowed),
                {"key": key, "value": fields[key], "allowed": list(allowed)},
            )


def validate_engine_fields(fields: Mapping[str, Any]) -> None:
    """Refuse engine fields whose values the written document cannot carry back."""
    if "acceptance" in fields and not isinstance(fields["acceptance"], bool):
        raise ModelError("VALIDATION_FAILED", "acceptance must be true or false")
    declared = fields.get("inputs")
    if declared is None:
        return
    if not isinstance(declared, (list, tuple)):
        raise ModelError("VALIDATION_FAILED", "inputs must be a list of globs")
    for glob in declared:
        if not isinstance(glob, str):
            raise ModelError("VALIDATION_FAILED", "an input glob must be a string")
        problem = invalid_glob(glob)
        if problem is not None:
            raise ModelError(
                "VALIDATION_FAILED",
                f"{glob!r} is not a valid input glob: {problem}",
            )


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}
