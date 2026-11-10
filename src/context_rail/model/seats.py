"""Seat records: who holds which role where, as immutable engine-owned records.

A seat is "actor X holds role R on anchor A". Grants and revokes are records
with their own schema, like runs; the live seat table is their fold. The record
kind, not a data key, tells a grant from a revoke.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from context_rail.model.fields import (
    mapping,
    no_unknown,
    refuse,
    require,
    schema_version,
    string,
)
from context_rail.model.roles import MAX_NAME_LENGTH, is_name

__all__ = [
    "GRANT_KEYS",
    "HUMAN",
    "REVOKE_KEYS",
    "SEAT_GRANTED",
    "SEAT_KINDS",
    "SEAT_REVOKED",
    "SEAT_SCHEMA",
    "SeatEvent",
    "parse_seat_event",
]

SEAT_SCHEMA = 1
SEAT_GRANTED = "seat.granted"
SEAT_REVOKED = "seat.revoked"
SEAT_KINDS = (SEAT_GRANTED, SEAT_REVOKED)
GRANT_KEYS = ("schema", "seat", "role", "anchor", "profile", "granted_by")
REVOKE_KEYS = ("schema", "seat", "revoked_by")
#: The granter name of a verified human. It is never a seat name.
HUMAN = "human"
_WHAT = "seat record"


@dataclass(frozen=True)
class SeatEvent:
    """One grant or revoke. ``by`` is the granter or the revoker."""

    kind: str
    seat: str
    role: str | None
    anchor: str | None
    profile: str | None
    by: str
    at: str
    record_id: str


def parse_seat_event(
    kind: str, data: dict[str, Any], *, at: str, record_id: str
) -> SeatEvent:
    """Validate one seat record's payload, refusing unknown or missing keys."""
    if kind not in SEAT_KINDS:
        refuse(f"unknown seat kind {kind!r}")
    payload = mapping(data, _WHAT)
    schema_version(payload, SEAT_SCHEMA, _WHAT)
    granted = kind == SEAT_GRANTED
    no_unknown(payload, GRANT_KEYS if granted else REVOKE_KEYS, _WHAT)
    require(
        payload,
        ("seat", "role", "anchor", "granted_by") if granted else ("seat", "revoked_by"),
        _WHAT,
    )
    seat = _seat_name(payload["seat"])
    if not granted:
        return SeatEvent(
            kind,
            seat,
            None,
            None,
            None,
            _by(payload["revoked_by"], "revoked_by"),
            at,
            record_id,
        )
    role = string(payload["role"], f"{_WHAT}.role")
    if not is_name(role):
        refuse(f"{_WHAT}.role {role!r} is not a valid role name")
    profile = payload.get("profile")
    if profile is not None:
        profile = string(profile, f"{_WHAT}.profile")
    return SeatEvent(
        kind,
        seat,
        role,
        string(payload["anchor"], f"{_WHAT}.anchor"),
        profile,
        _by(payload["granted_by"], "granted_by"),
        at,
        record_id,
    )


def _seat_name(value: Any) -> str:
    name = string(value, f"{_WHAT}.seat")
    if name == HUMAN or not is_name(name):
        refuse(
            f"{_WHAT}.seat {name!r} is not a valid seat name; "
            f"use a lowercase slug of at most {MAX_NAME_LENGTH} characters"
        )
    return name


def _by(value: Any, key: str) -> str:
    name = string(value, f"{_WHAT}.{key}")
    if name != HUMAN and not is_name(name):
        refuse(f"{_WHAT}.{key} {name!r} must be {HUMAN!r} or a seat name")
    return name
