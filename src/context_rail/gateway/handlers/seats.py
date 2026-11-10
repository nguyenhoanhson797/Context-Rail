"""Seat commands: which seats hold which roles, read from the seat records."""

from __future__ import annotations

from typing import Any

from context_rail.gateway.gateway import Context
from context_rail.kernel.seats import fold
from context_rail.model.seats import SEAT_GRANTED
from context_rail.store import query

__all__ = ["seat_list"]


def seat_list(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The live seats; ``all`` lists every grant and marks which one is live."""
    if not ctx.role_mode:
        return {"seats": [], "role_mode": False}
    events = query.seat_events(ctx.index)
    live = fold(events)
    rows: list[dict[str, Any]] = []
    if args.get("all"):
        for event in events:
            if event.kind != SEAT_GRANTED:
                continue
            current = live.get(event.seat)
            is_live = current is not None and (
                current.role,
                current.anchor,
                current.profile,
                current.granted_by,
                current.granted_at,
            ) == (event.role, event.anchor, event.profile, event.by, event.at)
            rows.append(
                _row(
                    event.seat,
                    event.role,
                    event.anchor,
                    event.profile,
                    event.by,
                    is_live,
                )
            )
    else:
        for seat in sorted(
            live.values(), key=lambda item: (item.granted_at, item.seat)
        ):
            rows.append(
                _row(
                    seat.seat,
                    seat.role,
                    seat.anchor,
                    seat.profile,
                    seat.granted_by,
                    True,
                )
            )
    return {"seats": rows, "role_mode": True}


def _row(
    seat: str,
    role: str | None,
    anchor: str | None,
    profile: str | None,
    granted_by: str,
    live: bool,
) -> dict[str, Any]:
    return {
        "seat": seat,
        "role": role,
        "anchor": anchor,
        "profile": profile,
        "granted_by": granted_by,
        "live": live,
    }
