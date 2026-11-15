"""Result artifact contracts, Markdown body checks, and immutable result payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from context_rail.core.util import canonical_iso, sha256_hex
from context_rail.model.candidate import TEXT_CAP
from context_rail.model.fields import (
    SLUG_RE,
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
from context_rail.model.roles import is_name

__all__ = [
    "ARTIFACT_KEYS",
    "RESULT_ADOPTION_KIND",
    "RESULT_ADOPTION_SCHEMA",
    "RESULT_BODY_MAX_BYTES",
    "RESULT_KEYS",
    "RESULT_KIND",
    "RESULT_OUTCOMES",
    "RESULT_PAYLOAD_KEYS",
    "RESULT_SCHEMA",
    "REVIEW_KEYS",
    "REVIEW_REQUIRED",
    "REVIEW_SCHEMA",
    "REVIEW_VERDICTS",
    "ArtifactSpec",
    "ResultBinding",
    "ResultPayload",
    "ResultProtocol",
    "ResultSelection",
    "extract_markdown_h2_headings",
    "missing_or_unordered_headings",
    "normalize_result_body",
    "parse_artifact_spec",
    "parse_result",
    "parse_results",
    "parse_review",
    "parse_workflow_results",
    "result_body_hash",
    "result_protocol_as_dict",
    "validate_result_body",
    "validate_template_headings",
    "workflow_results_as_dict",
]

RESULT_BODY_MAX_BYTES = 262144
RESULT_KIND = "result.recorded"
RESULT_ADOPTION_KIND = "results.adopted"
RESULT_SCHEMA = 1
RESULT_ADOPTION_SCHEMA = 1
REVIEW_SCHEMA = 1
REVIEW_VERDICTS = ("approve", "reject")
REVIEW_KEYS = (
    "schema",
    "item",
    "implementation_run",
    "implementer_actor",
    "implementer_seat",
    "implementer_profile",
    "reviewer_actor",
    "reviewer_seat",
    "reviewer_profile",
    "implementer_model",
    "reviewer_model",
    "candidate_commit",
    "result_record",
    "result_body_hash",
    "body_hash",
    "review_contract_digest",
    "verdict",
    "findings",
    "evidence",
    "profile_version",
    "candidate_record",
    "notes",
    "harness",
)
REVIEW_REQUIRED = ("schema", "item", "reviewer_actor", "verdict")
RESULT_OUTCOMES = ("completed", "partial", "blocked", "failed")
RESULT_PAYLOAD_KEYS = (
    "schema",
    "item",
    "attempt",
    "actor",
    "at",
    "outcome",
    "candidate_commit",
    "body_hash",
    "item_digest",
    "work_cycle",
    "contract_digest",
    "seat",
    "seat_role",
    "profile",
    "profile_version",
    "skills",
    "model",
    "harness",
)
_RESULT_REQUIRED_KEYS = (
    "schema",
    "item",
    "attempt",
    "actor",
    "outcome",
    "body_hash",
    "item_digest",
    "contract_digest",
)
RESULT_KEYS = ("produce", "review")
ARTIFACT_KEYS = ("preset", "sections")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FULL_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_SKILL_PIN_KEYS = ("identity", "version", "etag")


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


@dataclass(frozen=True)
class ResultBinding:
    """Immutable binding shared by producer results, reviews, checks and gates."""

    record: str
    body_hash: str
    item_digest: str
    work_cycle: str | None
    contract_digest: str
    candidate_commit: str | None


@dataclass(frozen=True)
class ResultPayload:
    """Validated ``result.recorded`` payload."""

    schema: int
    item: str
    attempt: int
    actor: str
    at: str | None
    outcome: str
    candidate_commit: str | None
    body_hash: str
    item_digest: str
    work_cycle: str | None
    contract_digest: str
    seat: str | None = None
    seat_role: str | None = None
    profile: str | None = None
    profile_version: int | None = None
    skills: tuple[dict[str, Any], ...] = ()
    model: str | None = None
    harness: str | None = None


@dataclass(frozen=True)
class ResultSelection:
    """Selected producer result alongside currentness, completion and diagnostic reasons."""

    record: Mapping[str, Any] | None
    binding: ResultBinding | None
    current: bool
    completed: bool
    reasons: tuple[str, ...]


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


def normalize_result_body(body: str) -> str:
    """Normalize a Markdown result body: strip leading UTF-8 BOM and normalize line endings to LF."""
    if not isinstance(body, str):
        refuse("result body must be text")
    if body.startswith("\ufeff"):
        body = body[1:]
    return body.replace("\r\n", "\n").replace("\r", "\n")


def result_body_hash(body: str) -> str:
    """Lowercase SHA-256 hex digest of the normalized Markdown result body."""
    normalized = normalize_result_body(body)
    return sha256_hex(normalized.encode("utf-8"))


def _extract_h2_sections(text: str) -> list[tuple[str, str]]:
    """Extract ordered ``(heading, section_body)`` pairs outside backtick/tilde fences."""
    normalized = normalize_result_body(text)
    sections: list[tuple[str, list[str]]] = []
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
                if char == "`" and "`" in trailing:
                    pass
                else:
                    fence_char = char
                    fence_len = length
                    if sections:
                        sections[-1][1].append(line)
                    continue
            elif char == fence_char and length >= fence_len and not trailing.strip():
                fence_char = None
                fence_len = 0
                if sections:
                    sections[-1][1].append(line)
                continue
        if fence_char is not None:
            if sections:
                sections[-1][1].append(line)
            continue
        heading_match = _H2_RE.match(line)
        if heading_match is not None:
            sections.append((heading_match.group(1).strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return [(heading, "\n".join(lines)) for heading, lines in sections]


def _has_meaningful_content(section_body: str) -> bool:
    stripped = _HTML_COMMENT_RE.sub("", section_body)
    return bool(stripped.strip())


def validate_result_body(body: str, sections: Sequence[str]) -> None:
    """Validate bounded UTF-8 Markdown body with exact ordered non-empty required headings."""
    normalized = normalize_result_body(body)
    raw_bytes = normalized.encode("utf-8")
    if len(raw_bytes) > RESULT_BODY_MAX_BYTES:
        refuse(
            f"result body is {len(raw_bytes)} bytes; max is {RESULT_BODY_MAX_BYTES} bytes"
        )
    if not normalized.strip():
        refuse("result body must not be empty")
    found_sections = _extract_h2_sections(normalized)
    found_names = [name for name, _ in found_sections]
    counts: dict[str, int] = {}
    for name in found_names:
        counts[name] = counts.get(name, 0) + 1
    for required in sections:
        if counts.get(required, 0) > 1:
            refuse(f"result body repeats required heading {required!r}")
    order_problem = missing_or_unordered_headings(found_names, sections)
    if order_problem is not None:
        refuse(f"result body {order_problem}")
    cursor = 0
    for required in sections:
        pos = found_names.index(required, cursor)
        _, content = found_sections[pos]
        if not _has_meaningful_content(content):
            refuse(f"result section {required!r} must have non-empty content")
        cursor = pos + 1


def parse_result(payload: Mapping[str, Any]) -> ResultPayload:
    """Validate one ``result.recorded`` payload, refusing unknown keys or malformed bindings."""
    data = mapping(dict(payload) if isinstance(payload, Mapping) else payload, "result")
    schema = schema_version(data, RESULT_SCHEMA, "result")
    no_unknown(data, RESULT_PAYLOAD_KEYS, "result")
    require(data, _RESULT_REQUIRED_KEYS, "result")

    item = string(data["item"], "result.item")
    if SLUG_RE.fullmatch(item) is None:
        refuse(f"result.item {item!r} must be a valid slug")
    attempt = integer(data["attempt"], "result.attempt", positive=True)
    actor = string(data["actor"], "result.actor")
    at: str | None = None
    if data.get("at") is not None:
        at = canonical_iso(string(data["at"], "result.at"))
        if at is None:
            refuse("result.at must be an ISO 8601 timestamp")
    outcome = string(data["outcome"], "result.outcome")
    if outcome not in RESULT_OUTCOMES:
        refuse("result.outcome must be one of " + ", ".join(RESULT_OUTCOMES))

    raw_commit = data.get("candidate_commit")
    candidate_commit: str | None = None
    if raw_commit is not None:
        candidate_commit = string(raw_commit, "result.candidate_commit")
        if _FULL_SHA1_RE.fullmatch(candidate_commit) is None:
            refuse("result.candidate_commit must be a 40-character lowercase hex sha")

    body_hash = _sha256(data["body_hash"], "result.body_hash")
    item_digest = _sha256(data["item_digest"], "result.item_digest")
    contract_digest = _sha256(data["contract_digest"], "result.contract_digest")

    raw_cycle = data.get("work_cycle")
    work_cycle: str | None = None
    if raw_cycle is not None:
        work_cycle = string(raw_cycle, "result.work_cycle")
        if _ULID_RE.fullmatch(work_cycle) is None:
            refuse("result.work_cycle must be a 26-character ULID or null")

    seat = _optional_name(data.get("seat"), "result.seat")
    seat_role = _optional_name(data.get("seat_role"), "result.seat_role")
    profile = _optional_text(data.get("profile"), "result.profile")
    profile_version = _optional_positive(data.get("profile_version"), "result.profile_version")
    skills = _skill_pins(data.get("skills", []))
    model = _optional_text(data.get("model"), "result.model")
    harness = _optional_text(data.get("harness"), "result.harness")

    return ResultPayload(
        schema=schema,
        item=item,
        attempt=attempt,
        actor=actor,
        at=at,
        outcome=outcome,
        candidate_commit=candidate_commit,
        body_hash=body_hash,
        item_digest=item_digest,
        work_cycle=work_cycle,
        contract_digest=contract_digest,
        seat=seat,
        seat_role=seat_role,
        profile=profile,
        profile_version=profile_version,
        skills=skills,
        model=model,
        harness=harness,
    )


def _sha256(raw: Any, what: str) -> str:
    text = string(raw, what)
    if _SHA256_RE.fullmatch(text) is None:
        refuse(f"{what} must be a 64-character lowercase hex sha256")
    return text


def _optional_name(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    text = string(raw, what)
    if not is_name(text):
        refuse(f"{what} {text!r} is not a valid slug name")
    return text


def _optional_text(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    text = string(raw, what)
    if len(text) > TEXT_CAP:
        refuse(f"{what} is limited to {TEXT_CAP} characters")
    return text


def _optional_positive(raw: Any, what: str) -> int | None:
    if raw is None:
        return None
    return integer(raw, what, positive=True)


def _skill_pins(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        refuse("result.skills must be a list")
    pins: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        what = f"result.skills[{index}]"
        pin = mapping(entry, what)
        no_unknown(pin, _SKILL_PIN_KEYS, what)
        require(pin, _SKILL_PIN_KEYS, what)
        pins.append(
            {
                "identity": relative_path(pin["identity"], f"{what}.identity"),
                "version": integer(pin["version"], f"{what}.version", positive=True),
                "etag": string(pin["etag"], f"{what}.etag"),
            }
        )
    return tuple(pins)


def parse_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one ``review.verdict`` payload, covering legacy and Markdown result reviews."""
    data = mapping(dict(payload) if isinstance(payload, Mapping) else payload, "review")
    schema_version(data, REVIEW_SCHEMA, "review")
    no_unknown(data, REVIEW_KEYS, "review")
    require(data, REVIEW_REQUIRED, "review")

    string(data["item"], "review.item")
    if data.get("implementation_run") is not None:
        string(data["implementation_run"], "review.implementation_run")
    for name in (
        "implementer_actor",
        "implementer_seat",
        "implementer_profile",
        "reviewer_seat",
        "reviewer_profile",
        "implementer_model",
        "reviewer_model",
        "candidate_record",
        "notes",
        "harness",
    ):
        if data.get(name) is not None:
            string(data[name], f"review.{name}")
    string(data["reviewer_actor"], "review.reviewer_actor")

    raw_commit = data.get("candidate_commit")
    candidate_commit = (
        None if raw_commit is None else string(raw_commit, "review.candidate_commit")
    )

    raw_res = data.get("result_record")
    result_record: str | None = None
    if raw_res is not None:
        result_record = string(raw_res, "review.result_record")
        if _ULID_RE.fullmatch(result_record) is None:
            refuse("review.result_record must be a 26-character ULID")

    raw_res_hash = data.get("result_body_hash")
    result_body_hash_val = (
        None
        if raw_res_hash is None
        else _sha256(raw_res_hash, "review.result_body_hash")
    )

    if (result_record is None) != (result_body_hash_val is None):
        refuse("review.result_record and review.result_body_hash must appear together")

    raw_body_hash = data.get("body_hash")
    body_hash_val = (
        None if raw_body_hash is None else _sha256(raw_body_hash, "review.body_hash")
    )

    raw_contract = data.get("review_contract_digest")
    contract_digest_val = (
        None
        if raw_contract is None
        else _sha256(raw_contract, "review.review_contract_digest")
    )

    if (body_hash_val is None) != (contract_digest_val is None):
        refuse(
            "review.body_hash and review.review_contract_digest must appear together"
        )

    verdict = string(data["verdict"], "review.verdict")
    if verdict not in REVIEW_VERDICTS:
        refuse("review.verdict must be approve or reject")

    is_contract_review = contract_digest_val is not None
    if is_contract_review:
        if candidate_commit is not None and _FULL_SHA1_RE.fullmatch(candidate_commit) is None:
            refuse("review.candidate_commit must be a 40-character lowercase hex sha")
        if verdict == "approve" and result_record is None:
            refuse("contract-enabled approval review requires result_record")
    else:
        if candidate_commit is None and result_record is None:
            refuse("review needs candidate_commit or result_record")

    for name in ("findings", "evidence"):
        if data.get(name) is not None:
            string_list(data[name], f"review.{name}", allow_empty=True)
    if data.get("profile_version") is not None:
        integer(data["profile_version"], "review.profile_version", positive=True)

    return dict(data)

