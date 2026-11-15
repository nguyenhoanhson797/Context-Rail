"""Strict payloads for candidate records.

A candidate record says that one actor produced one commit for one item. Rail
never runs the agent that produced it: any tool, with or without sub-agents,
records the commit it made. Model and harness names are free text and are never
checked against a list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from context_rail.core.util import canonical_iso
from context_rail.model.fields import (
    boolean,
    finite_number,
    integer,
    is_number,
    mapping,
    no_unknown,
    refuse,
    relative_path,
    require,
    schema_version,
    string,
    string_list,
)

__all__ = [
    "CANDIDATE_KEYS",
    "CANDIDATE_KIND",
    "CANDIDATE_SCHEMA",
    "DIFF_TEXT_CAP",
    "FILES_CAP",
    "TEXT_CAP",
    "UNKNOWN_USAGE",
    "USAGE_KEY",
    "USAGE_MAX_KEYS",
    "Candidate",
    "parse_budget_caps",
    "parse_candidate",
    "parse_usage_map",
]

CANDIDATE_KIND = "candidate.recorded"
CANDIDATE_SCHEMA = 1
DIFF_TEXT_CAP = 6000
FILES_CAP = 500
TEXT_CAP = 200
USAGE_MAX_KEYS = 32
UNKNOWN_USAGE = "unknown"
USAGE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LEGACY_CAPS = ("attempts", "seconds", "tokens", "credits")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

CANDIDATE_KEYS = (
    "schema",
    "item",
    "commit",
    "base",
    "files_changed",
    "files_truncated",
    "diff",
    "diff_truncated",
    "attempt",
    "actor",
    "seat",
    "seat_role",
    "profile",
    "profile_version",
    "skills",
    "model",
    "harness",
    "usage",
    "duration_s",
    "at",
)
_REQUIRED = ("schema", "item", "commit", "base", "files_changed", "attempt", "actor", "at")
_SKILL_PIN_KEYS = ("identity", "version", "etag")


@dataclass(frozen=True)
class Candidate:
    """One recorded commit for one item, as its payload states it."""

    schema: int
    item: str
    commit: str
    base: str
    files_changed: tuple[str, ...]
    attempt: int
    actor: str
    at: str
    files_truncated: bool = False
    diff: str | None = None
    diff_truncated: bool = False
    seat: str | None = None
    seat_role: str | None = None
    profile: str | None = None
    profile_version: int | None = None
    skills: tuple[dict[str, Any], ...] = ()
    model: str | None = None
    harness: str | None = None
    usage: dict[str, int | float | str] = field(default_factory=dict)
    duration_s: int | float | None = None


def parse_candidate(payload: dict[str, Any]) -> Candidate:
    """Validate one ``candidate.recorded`` payload, refusing every unknown key."""
    data = mapping(payload, "candidate")
    schema = schema_version(data, CANDIDATE_SCHEMA, "candidate")
    no_unknown(data, CANDIDATE_KEYS, "candidate")
    require(data, _REQUIRED, "candidate")

    files = string_list(data["files_changed"], "candidate.files_changed", allow_empty=True)
    if len(files) > FILES_CAP:
        refuse(f"candidate.files_changed holds at most {FILES_CAP} paths")
    at = canonical_iso(string(data["at"], "candidate.at"))
    if at is None:
        refuse("candidate.at must be an ISO 8601 timestamp")

    return Candidate(
        schema=schema,
        item=string(data["item"], "candidate.item"),
        commit=_sha(data["commit"], "candidate.commit"),
        base=_sha(data["base"], "candidate.base"),
        files_changed=files,
        attempt=integer(data["attempt"], "candidate.attempt", positive=True),
        actor=string(data["actor"], "candidate.actor"),
        at=at,
        files_truncated=boolean(data.get("files_truncated", False), "candidate.files_truncated"),
        diff=_optional_diff(data.get("diff")),
        diff_truncated=boolean(data.get("diff_truncated", False), "candidate.diff_truncated"),
        seat=_optional_text(data.get("seat"), "candidate.seat"),
        seat_role=_optional_text(data.get("seat_role"), "candidate.seat_role"),
        profile=_optional_text(data.get("profile"), "candidate.profile"),
        profile_version=_optional_positive(
            data.get("profile_version"), "candidate.profile_version"
        ),
        skills=_skill_pins(data.get("skills", [])),
        model=_optional_text(data.get("model"), "candidate.model"),
        harness=_optional_text(data.get("harness"), "candidate.harness"),
        usage=parse_usage_map(data.get("usage", {}), "candidate.usage"),
        duration_s=(
            None
            if data.get("duration_s") is None
            else finite_number(data["duration_s"], "candidate.duration_s", minimum=0)
        ),
    )


def parse_usage_map(raw: Any, what: str) -> dict[str, int | float | str]:
    """Validate an open usage map: counters are named by the reporting tool.

    A value is a finite non-negative number or the literal ``"unknown"``, which
    is kept apart from zero everywhere it is summed.
    """
    usage = mapping(raw, what)
    if len(usage) > USAGE_MAX_KEYS:
        refuse(f"{what} holds at most {USAGE_MAX_KEYS} counters")
    parsed: dict[str, int | float | str] = {}
    for key, value in usage.items():
        if not USAGE_KEY.fullmatch(key):
            refuse(f"{what} counter {key!r} must match {USAGE_KEY.pattern}")
        if isinstance(value, str) and value == UNKNOWN_USAGE:
            parsed[key] = UNKNOWN_USAGE
        elif is_number(value) and value >= 0:
            parsed[key] = value
        else:
            refuse(f'{what}.{key} must be a non-negative number or "{UNKNOWN_USAGE}"')
    return parsed


def parse_budget_caps(raw: Any, what: str) -> dict[str, int | float | None]:
    """Validate one budget scope: caps on attempts, seconds or any usage counter.

    The four long-standing names are always present, None when unset. Any other
    key must be a usage counter name, so a cap can only ever measure something a
    candidate can report; a number is never guessed from a misspelt key.
    """
    entry = mapping(raw, what)
    if len(entry) > USAGE_MAX_KEYS + 2:
        refuse(f"{what} holds at most {USAGE_MAX_KEYS + 2} caps")
    caps: dict[str, int | float | None] = dict.fromkeys(_LEGACY_CAPS)
    for key, value in entry.items():
        if key not in ("attempts", "seconds") and not USAGE_KEY.fullmatch(key):
            refuse(f"{what} cap {key!r} must be attempts, seconds or match {USAGE_KEY.pattern}")
        where = f"{what}.{key}"
        if value is None:
            caps[key] = None
        elif key == "attempts":
            caps[key] = integer(value, where, positive=True)
        elif key == "seconds":
            caps[key] = finite_number(value, where, positive=True)
        else:
            caps[key] = finite_number(value, where, minimum=0)
    return caps


def _sha(raw: Any, what: str) -> str:
    text = string(raw, what)
    if not _FULL_SHA.fullmatch(text):
        refuse(f"{what} must be a full 40-character lowercase hex sha")
    return text


def _optional_text(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    text = string(raw, what)
    if len(text) > TEXT_CAP:
        refuse(f"{what} is limited to {TEXT_CAP} characters")
    return text


def _optional_diff(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        refuse("candidate.diff must be a string")
    if len(raw) > DIFF_TEXT_CAP:
        refuse(f"candidate.diff is limited to {DIFF_TEXT_CAP} characters")
    return raw


def _optional_positive(raw: Any, what: str) -> int | None:
    if raw is None:
        return None
    return integer(raw, what, positive=True)


def _skill_pins(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        refuse("candidate.skills must be a list")
    pins: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        what = f"candidate.skills[{index}]"
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
