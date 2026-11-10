"""Seat records: engine-owned schema, strict keys, slug names."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.seats import (
    GRANT_KEYS,
    REVOKE_KEYS,
    SEAT_GRANTED,
    SEAT_KINDS,
    SEAT_REVOKED,
    SEAT_SCHEMA,
    SeatEvent,
    parse_seat_event,
)

AT = "2026-11-10T10:00:00Z"


def _grant(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": SEAT_SCHEMA,
        "seat": "manager-1",
        "role": "feature-manager",
        "anchor": "orders",
        "profile": "manager",
        "granted_by": "lead-1",
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not _DROP}


_DROP = object()


def _parse(kind: str, data: dict[str, Any]) -> SeatEvent:
    return parse_seat_event(kind, data, at=AT, record_id="rec-1")


def test_the_vocabulary_is_fixed() -> None:
    assert SEAT_SCHEMA == 1
    assert SEAT_KINDS == ("seat.granted", "seat.revoked")
    assert GRANT_KEYS == ("schema", "seat", "role", "anchor", "profile", "granted_by")
    assert REVOKE_KEYS == ("schema", "seat", "revoked_by")


def test_a_grant_parses() -> None:
    event = _parse(SEAT_GRANTED, _grant())

    assert event == SeatEvent(
        kind=SEAT_GRANTED,
        seat="manager-1",
        role="feature-manager",
        anchor="orders",
        profile="manager",
        by="lead-1",
        at=AT,
        record_id="rec-1",
    )


def test_a_grant_may_omit_the_profile_and_name_the_human() -> None:
    event = _parse(
        SEAT_GRANTED, _grant(profile=_DROP, granted_by="human", anchor="project")
    )

    assert event.profile is None
    assert event.by == "human"
    assert event.anchor == "project"


def test_a_revoke_parses() -> None:
    data = {"schema": 1, "seat": "manager-1", "revoked_by": "human"}

    event = _parse(SEAT_REVOKED, data)

    assert (event.kind, event.seat, event.by) == (SEAT_REVOKED, "manager-1", "human")
    assert event.role is None and event.anchor is None and event.profile is None


@pytest.mark.parametrize(
    ("kind", "data", "needle"),
    [
        pytest.param(
            SEAT_GRANTED, _grant(mood="calm"), "unknown key", id="grant-unknown-key"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(role=_DROP), "missing role", id="grant-missing-role"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(anchor=_DROP), "missing anchor", id="grant-no-anchor"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(granted_by=_DROP), "granted_by", id="grant-no-granter"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(seat=_DROP), "missing seat", id="grant-no-seat"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(seat="Manager 1"), "seat name", id="seat-not-a-slug"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(seat="1st"), "seat name", id="seat-starts-with-digit"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(seat="m_1"), "seat name", id="seat-underscore"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(seat="a" * 41), "seat name", id="seat-too-long"
        ),
        pytest.param(
            SEAT_GRANTED, _grant(role="Feature Manager"), "role", id="role-not-a-slug"
        ),
        pytest.param(
            SEAT_GRANTED,
            _grant(granted_by="Lead One"),
            "granted_by",
            id="granter-not-a-slug",
        ),
        pytest.param(SEAT_GRANTED, _grant(anchor=""), "anchor", id="empty-anchor"),
        pytest.param(
            SEAT_GRANTED, _grant(profile=3), "profile", id="profile-not-a-string"
        ),
        pytest.param(SEAT_GRANTED, _grant(seat=7), "seat", id="seat-not-a-string"),
        pytest.param(
            SEAT_REVOKED,
            {"schema": 1, "seat": "m", "role": "x", "revoked_by": "h"},
            "unknown key",
            id="revoke-with-grant-key",
        ),
        pytest.param(
            SEAT_REVOKED,
            {"schema": 1, "seat": "m"},
            "revoked_by",
            id="revoke-no-revoker",
        ),
        pytest.param(
            SEAT_REVOKED,
            {"schema": 1, "seat": "m", "revoked_by": "Nobody Here"},
            "revoked_by",
            id="revoker-not-a-slug",
        ),
        pytest.param("seat.moved", _grant(), "seat kind", id="unknown-kind"),
    ],
)
def test_a_bad_seat_record_is_refused(
    kind: str, data: dict[str, Any], needle: str
) -> None:
    with pytest.raises(ModelError) as caught:
        _parse(kind, data)

    assert needle in caught.value.message


def test_a_wrong_schema_is_unsupported() -> None:
    with pytest.raises(ModelError) as caught:
        _parse(SEAT_GRANTED, _grant(schema=2))

    assert caught.value.code == "SCHEMA_UNSUPPORTED"


def test_a_missing_schema_is_refused() -> None:
    with pytest.raises(ModelError) as caught:
        _parse(SEAT_GRANTED, _grant(schema=_DROP))

    assert caught.value.code == "VALIDATION_FAILED"
    assert "schema" in caught.value.message


def test_the_human_name_may_not_be_a_seat() -> None:
    with pytest.raises(ModelError) as caught:
        _parse(SEAT_GRANTED, _grant(seat="human"))

    assert "seat name" in caught.value.message
