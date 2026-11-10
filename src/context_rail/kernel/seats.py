"""The live-seat fold: grants and revokes in time order, with lineage cascade."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from context_rail.model.seats import HUMAN, SEAT_GRANTED, SeatEvent

__all__ = ["LiveSeat", "fold"]


@dataclass(frozen=True)
class LiveSeat:
    """One seat that is live after every record has been applied."""

    seat: str
    role: str
    anchor: str
    profile: str | None
    granted_by: str
    granted_at: str


def fold(events: Sequence[SeatEvent]) -> dict[str, LiveSeat]:
    """The live seats after ``events``, applied in ``(at, record_id)`` order.

    A grant of a name that is already live changes nothing, and so does a grant
    whose granter is neither the human nor a live seat: neither can widen the
    table. A revoke removes the seat and every seat granted through it.
    """
    live: dict[str, LiveSeat] = {}
    for event in sorted(events, key=lambda item: (item.at, item.record_id)):
        if event.kind == SEAT_GRANTED:
            if event.role is None or event.anchor is None or event.seat in live:
                continue
            if event.by != HUMAN and event.by not in live:
                continue
            live[event.seat] = LiveSeat(
                event.seat, event.role, event.anchor, event.profile, event.by, event.at
            )
        elif event.seat in live:
            for name in _lineage(live, event.seat):
                del live[name]
    return live


def _lineage(live: dict[str, LiveSeat], root: str) -> list[str]:
    found = [root]
    for name in found:
        found.extend(
            seat.seat
            for seat in live.values()
            if seat.granted_by == name and seat.seat not in found
        )
    return found
