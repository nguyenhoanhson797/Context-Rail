"""The live-seat fold: grants and revokes in time order, with lineage cascade."""

from __future__ import annotations

from context_rail.kernel.seats import LiveSeat, fold
from context_rail.model.seats import SEAT_GRANTED, SEAT_REVOKED, SeatEvent


def _grant(
    seat: str,
    by: str,
    at: str,
    rid: str,
    *,
    role: str = "worker",
    anchor: str = "project",
) -> SeatEvent:
    return SeatEvent(SEAT_GRANTED, seat, role, anchor, None, by, at, rid)


def _revoke(seat: str, at: str, rid: str, by: str = "human") -> SeatEvent:
    return SeatEvent(SEAT_REVOKED, seat, None, None, None, by, at, rid)


def test_grant_makes_a_seat_live() -> None:
    live = fold([_grant("lead-1", "human", "2026-11-10T10:00:00Z", "a", role="lead")])

    assert live == {
        "lead-1": LiveSeat(
            seat="lead-1",
            role="lead",
            anchor="project",
            profile=None,
            granted_by="human",
            granted_at="2026-11-10T10:00:00Z",
        )
    }


def test_revoke_removes_the_seat() -> None:
    live = fold(
        [
            _grant("lead-1", "human", "2026-11-10T10:00:00Z", "a"),
            _revoke("lead-1", "2026-11-10T11:00:00Z", "b"),
        ]
    )

    assert live == {}


def test_revoking_a_granter_revokes_its_whole_lineage() -> None:
    live = fold(
        [
            _grant("lead", "human", "2026-11-10T10:00:00Z", "a"),
            _grant("manager", "lead", "2026-11-10T10:01:00Z", "b"),
            _grant("worker", "manager", "2026-11-10T10:02:00Z", "c"),
            _revoke("lead", "2026-11-10T11:00:00Z", "d"),
        ]
    )

    assert live == {}


def test_a_sibling_lineage_survives() -> None:
    live = fold(
        [
            _grant("lead-a", "human", "2026-11-10T10:00:00Z", "a"),
            _grant("lead-b", "human", "2026-11-10T10:00:01Z", "b"),
            _grant("worker-a", "lead-a", "2026-11-10T10:01:00Z", "c"),
            _grant("worker-b", "lead-b", "2026-11-10T10:01:01Z", "d"),
            _revoke("lead-a", "2026-11-10T11:00:00Z", "e"),
        ]
    )

    assert set(live) == {"lead-b", "worker-b"}


def test_regrant_after_revoke_is_live_without_the_old_lineage() -> None:
    live = fold(
        [
            _grant("lead", "human", "2026-11-10T10:00:00Z", "a"),
            _grant("worker", "lead", "2026-11-10T10:01:00Z", "b"),
            _revoke("lead", "2026-11-10T11:00:00Z", "c"),
            _grant("lead", "human", "2026-11-10T12:00:00Z", "d", anchor="orders"),
        ]
    )

    assert set(live) == {"lead"}
    assert live["lead"].anchor == "orders"


def test_events_apply_in_time_then_id_order() -> None:
    first = _grant("lead", "human", "2026-11-10T10:00:00Z", "a")
    revoke = _revoke("lead", "2026-11-10T10:00:00Z", "b")
    tied = [revoke, first]

    assert fold(tied) == {}
    assert fold(list(reversed(tied))) == {}
    later = _grant("lead", "human", "2026-11-10T09:59:59Z", "z")
    assert fold([revoke, later]) == {}


def test_a_second_grant_of_a_live_name_changes_nothing() -> None:
    live = fold(
        [
            _grant(
                "lead",
                "human",
                "2026-11-10T10:00:00Z",
                "a",
                role="lead",
                anchor="project",
            ),
            _grant(
                "lead",
                "human",
                "2026-11-10T10:01:00Z",
                "b",
                role="other",
                anchor="orders",
            ),
        ]
    )

    assert live["lead"].role == "lead"
    assert live["lead"].anchor == "project"


def test_a_grant_by_a_seat_that_is_not_live_is_ignored() -> None:
    live = fold(
        [
            _grant("worker", "ghost", "2026-11-10T10:00:00Z", "a"),
            _grant("lead", "human", "2026-11-10T10:01:00Z", "b"),
            _grant("later", "lead", "2026-11-10T10:02:00Z", "c"),
        ]
    )

    assert set(live) == {"lead", "later"}


def test_revoking_a_seat_that_is_not_live_changes_nothing() -> None:
    live = fold(
        [
            _revoke("ghost", "2026-11-10T09:00:00Z", "a"),
            _grant("lead", "human", "2026-11-10T10:00:00Z", "b"),
        ]
    )

    assert set(live) == {"lead"}
