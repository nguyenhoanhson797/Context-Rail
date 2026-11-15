"""Independence and code reach, judged over the records an item already holds.

Every step compared here is a record some actor wrote: a candidate, a review
verdict or a seat grant. No agent runtime is involved, so the same rules cover a
tool that opened a seat and one that only named itself with ``--actor``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from context_rail.context.reach import ReachError, resolve_reach
from context_rail.model.seats import SEAT_GRANTED

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.kernel.seats import LiveSeat
    from context_rail.model.roles import Role

__all__ = ["code_reach_violations", "independence_refusal"]


def independence_refusal(
    ctx: Context,
    *,
    item: str,
    step_kinds: Sequence[str],
    acting: Sequence[str],
) -> str | None:
    """Why the acting names are not independent of the item's listed steps.

    ``acting`` names the acting seat or actor and its profile. A ``produce``
    step is compared by the seat (else the actor) and the profile of every
    candidate on the item, a ``review`` step by the reviewer of every review,
    and a ``grant`` step by whoever granted a seat anchored at the item.
    """
    names = {name for name in acting if isinstance(name, str) and name}
    if not names:
        return None
    for kind in step_kinds:
        overlap = sorted(names & _prior_names(ctx, item, kind))
        if overlap:
            return f"the {kind} step on {item!r} was already performed by " + ", ".join(
                overlap
            )
    return None


def _prior_names(ctx: Context, item: str, kind: str) -> set[str]:
    conn = ctx.index.conn
    found: set[str] = set()
    if kind == "produce":
        for seat, actor, data_json in conn.execute(
            "SELECT seat, actor, data_json FROM candidates WHERE item = ?", (item,)
        ):
            found.add(seat or actor)
            profile = json.loads(data_json).get("profile")
            if isinstance(profile, str) and profile:
                found.add(profile)
        for actor, data_json in conn.execute(
            "SELECT actor, data_json FROM results WHERE item = ?", (item,)
        ):
            try:
                data = json.loads(data_json)
            except (TypeError, ValueError):
                data = {}
            seat = data.get("seat") if isinstance(data, dict) else None
            if isinstance(seat, str) and seat:
                found.add(seat)
            elif isinstance(actor, str) and actor:
                found.add(actor)
            profile = data.get("profile") if isinstance(data, dict) else None
            if isinstance(profile, str) and profile:
                found.add(profile)
    elif kind == "review":
        found.update(
            reviewer
            for (reviewer,) in conn.execute(
                "SELECT reviewer FROM reviews WHERE item = ?", (item,)
            )
            if reviewer
        )
    elif kind == "grant":
        found.update(
            granter
            for (granter,) in conn.execute(
                "SELECT actor_by FROM seats WHERE kind = ? AND anchor = ?",
                (SEAT_GRANTED, item),
            )
        )
    return found


def code_reach_violations(
    ctx: Context, *, seat: LiveSeat, role: Role, files: Sequence[str]
) -> list[str]:
    """The changed files outside the seat's code write reach.

    A seat whose reach cannot be resolved fails closed: every changed file is a
    violation, never none.
    """
    paths = [path.replace("\\", "/") for path in files if isinstance(path, str)]
    try:
        reach = resolve_reach(ctx.index, ctx.pack, role, seat)
    except ReachError:
        return paths
    return [path for path in paths if not reach.can_write_code(path)]
