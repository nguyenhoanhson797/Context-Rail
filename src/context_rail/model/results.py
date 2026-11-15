"""Result artifact contracts and Markdown heading validation helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from context_rail.model.fields import mapping, no_unknown, refuse, string, string_list

__all__ = [
    "ARTIFACT_KEYS",
    "RESULT_BODY_MAX_BYTES",
    "RESULT_KEYS",
    "ArtifactSpec",
    "ResultProtocol",
    "extract_markdown_h2_headings",
    "missing_or_unordered_headings",
    "parse_artifact_spec",
    "parse_results",
    "parse_workflow_results",
    "result_protocol_as_dict",
    "validate_template_headings",
    "workflow_results_as_dict",
]

RESULT_BODY_MAX_BYTES = 262144
RESULT_KEYS = ("produce", "review")
ARTIFACT_KEYS = ("preset", "sections")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")


@dataclass(frozen=True)
class ArtifactSpec:
    """One artifact contract: a record preset and its ordered level-2 headings."""

    preset: str
    sections: tuple[str, ...]


@dataclass(frozen=True)
class ResultProtocol:
    """The producer and reviewer artifact contracts for a pack or workflow."""

    produce: ArtifactSpec
    review: ArtifactSpec


def _validate_section_names(raw: Any, where: str) -> tuple[str, ...]:
    names = string_list(raw, f"{where}.sections")
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in names:
        if "\n" in entry or "\r" in entry:
            refuse(f"{where}.sections heading {entry!r} must not contain newlines")
        heading = entry.strip()
        if not heading or heading != entry:
            refuse(f"{where}.sections heading {entry!r} must not have leading/trailing whitespace or be blank")
        if heading.startswith("#"):
            refuse(f"{where}.sections heading {entry!r} must be a plain heading name without '#'")
        if heading in seen:
            refuse(f"{where}.sections repeats heading {heading!r}")
        seen.add(heading)
        cleaned.append(heading)
    return tuple(cleaned)


def _check_record_preset(name: str, presets: Mapping[str, Any], where: str) -> None:
    preset = presets.get(name)
    if preset is None:
        refuse(f"{where}.preset names unknown preset {name!r}")
    if getattr(preset, "primitive", None) != "record":
        refuse(f"{where}.preset {name!r} must have primitive 'record'")


def parse_artifact_spec(
    raw: Any,
    *,
    presets: Mapping[str, Any],
    where: str,
    validate_presets: bool = True,
) -> ArtifactSpec:
    """Parse one complete artifact specification (``preset`` and ``sections``)."""
    body = mapping(raw, where)
    no_unknown(body, ARTIFACT_KEYS, where)
    for key in ARTIFACT_KEYS:
        if key not in body:
            refuse(f"{where} is missing {key}")
    preset = string(body["preset"], f"{where}.preset")
    if "\n" in preset or "\r" in preset:
        refuse(f"{where}.preset must be a single-line preset name")
    if validate_presets:
        _check_record_preset(preset, presets, where)
    sections = _validate_section_names(body["sections"], where)
    return ArtifactSpec(preset=preset, sections=sections)


def parse_results(
    raw: Any,
    *,
    presets: Mapping[str, Any],
    where: str,
    inherited: ResultProtocol | None = None,
    validate_presets: bool = True,
) -> ResultProtocol:
    """Parse pack-level ``results``, requiring both ``produce`` and ``review`` after inheritance."""
    body = mapping(raw, where)
    no_unknown(body, RESULT_KEYS, where)
    if not body:
        refuse(f"{where} must declare produce and/or review")

    parsed: dict[str, ArtifactSpec] = {}
    for role in RESULT_KEYS:
        if role in body:
            parsed[role] = parse_artifact_spec(
                body[role],
                presets=presets,
                where=f"{where}.{role}",
                validate_presets=validate_presets,
            )
        elif inherited is not None:
            spec = getattr(inherited, role)
            if validate_presets:
                _check_record_preset(spec.preset, presets, f"{where}.{role}")
            parsed[role] = spec
        else:
            refuse(f"{where} is missing {role}")

    return ResultProtocol(produce=parsed["produce"], review=parsed["review"])


def parse_workflow_results(
    raw: Any,
    *,
    presets: Mapping[str, Any],
    where: str,
    validate_presets: bool = True,
) -> dict[str, ArtifactSpec]:
    """Parse workflow-level ``results`` overrides (either or both complete specs)."""
    body = mapping(raw, where)
    no_unknown(body, RESULT_KEYS, where)
    if not body:
        refuse(f"{where} must override produce and/or review")
    overrides: dict[str, ArtifactSpec] = {}
    for role in RESULT_KEYS:
        if role in body:
            overrides[role] = parse_artifact_spec(
                body[role],
                presets=presets,
                where=f"{where}.{role}",
                validate_presets=validate_presets,
            )
    return overrides


def validate_result_protocol(
    protocol: ResultProtocol,
    *,
    presets: Mapping[str, Any],
    where: str,
) -> None:
    """Validate an inherited or merged :class:`ResultProtocol` against merged presets."""
    _check_record_preset(protocol.produce.preset, presets, f"{where}.produce")
    _check_record_preset(protocol.review.preset, presets, f"{where}.review")


def validate_workflow_overrides(
    overrides: Mapping[str, ArtifactSpec],
    *,
    presets: Mapping[str, Any],
    where: str,
) -> None:
    """Validate workflow-level overrides against merged presets."""
    for role, spec in overrides.items():
        if role not in RESULT_KEYS:
            refuse(f"{where} has unknown key {role!r}")
        _check_record_preset(spec.preset, presets, f"{where}.{role}")


def artifact_spec_as_dict(spec: ArtifactSpec) -> dict[str, Any]:
    return {"preset": spec.preset, "sections": list(spec.sections)}


def result_protocol_as_dict(protocol: ResultProtocol) -> dict[str, Any]:
    return {
        "produce": artifact_spec_as_dict(protocol.produce),
        "review": artifact_spec_as_dict(protocol.review),
    }


def workflow_results_as_dict(overrides: Mapping[str, ArtifactSpec]) -> dict[str, Any]:
    return {
        role: artifact_spec_as_dict(overrides[role])
        for role in RESULT_KEYS
        if role in overrides
    }


def extract_markdown_h2_headings(text: str) -> tuple[str, ...]:
    """Extract ordered ``## <Heading>`` names outside backtick or tilde fenced blocks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    headings: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in normalized.split("\n"):
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)
            trailing = fence_match.group(2)
            char = marker[0]
            length = len(marker)
            if fence_char is None:
                # CommonMark: backtick info strings cannot contain backticks.
                if char == "`" and "`" in trailing:
                    pass
                else:
                    fence_char = char
                    fence_len = length
                    continue
            elif char == fence_char and length >= fence_len and not trailing.strip():
                fence_char = None
                fence_len = 0
                continue
        if fence_char is not None:
            continue
        heading_match = _H2_RE.match(line)
        if heading_match is not None:
            headings.append(heading_match.group(1).strip())
    return tuple(headings)


def missing_or_unordered_headings(
    found: Sequence[str], required: Sequence[str]
) -> str | None:
    """Return a human-readable mismatch reason, or ``None`` when ``required`` appear in order."""
    cursor = 0
    for heading in required:
        try:
            pos = list(found).index(heading, cursor)
        except ValueError:
            if heading in found:
                return f"required heading {heading!r} appears out of order"
            return f"missing required heading {heading!r}"
        cursor = pos + 1
    return None


def validate_template_headings(
    text: str, required: Sequence[str], *, template: str
) -> str | None:
    """Validate that a Markdown template declares all ``required`` headings in order."""
    found = extract_markdown_h2_headings(text)
    problem = missing_or_unordered_headings(found, required)
    if problem is not None:
        return f"template {template!r} {problem}"
    return None
