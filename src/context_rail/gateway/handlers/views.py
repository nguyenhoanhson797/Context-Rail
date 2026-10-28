"""Read views: find, show and the status picture, all from the index."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from context_rail.gateway.handlers.common import parse_relation_filter
from context_rail.kernel import structure
from context_rail.model.primitives import CATEGORIES
from context_rail.store import query
from context_rail.store.write import resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

LIST_CAP = 20


def find(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Page through the documents matching every filter."""
    return query.find(
        ctx.index,
        primitive=args["primitive"],
        preset=args["preset"],
        tags=tuple(args["tag"]),
        status=args["status"],
        category=args["category"],
        text=args["text"],
        rel=parse_relation_filter(args["rel"]),
        limit=args["limit"],
    )


def show(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """One document, its relations and a bounded body."""
    index = ctx.index  # opened and synced before the one body read
    resolve_unique(index, args["slug"])
    return query.show(index, args["slug"], body_chars=args["body_chars"])


def status(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Counts, executable items, blockers and stuck items, each list capped."""
    index = ctx.index
    rows = index.conn.execute(
        "SELECT slug, category FROM docs WHERE primitive = 'item' AND broken = 0"
    ).fetchall()
    edges: dict[str, dict[str, list[str]]] = {}
    for slug, name, target in index.conn.execute(
        "SELECT r.src, r.rel, r.dst FROM relations r JOIN docs d ON d.path = r.src_path "
        "WHERE d.primitive = 'item' AND d.broken = 0 "
        "AND r.rel IN ('part_of', 'depends_on')"
    ):
        edges.setdefault(slug, {}).setdefault(name, []).append(target)
    activity = query.last_activity_ms(index)

    counts = {category: 0 for category in CATEGORIES}
    items: list[structure.ItemRow] = []
    for slug, category in rows:
        if category in counts:
            counts[category] += 1
        related = edges.get(slug, {})
        items.append(
            structure.ItemRow(
                slug=slug,
                category=category,
                part_of=tuple(sorted(related.get("part_of", ()))),
                depends_on=tuple(sorted(related.get("depends_on", ()))),
                last_activity_ms=activity.get(slug),
            )
        )

    runnable = sorted(set(structure.executable(items)))
    stalled = sorted(set(structure.stuck(items, _now_ms(), _stuck_ms(ctx))))
    held = sorted(structure.blocked(items).items())
    executable_page, executable_omitted = _page(runnable)
    blocked_page, blocked_omitted = _page(held)
    stuck_page, stuck_omitted = _page(stalled)
    return {
        "counts": counts,
        "executable": executable_page,
        "blocked": dict(blocked_page),
        "stuck": stuck_page,
        "omitted": {
            "executable": executable_omitted,
            "blocked": blocked_omitted,
            "stuck": stuck_omitted,
        },
    }


def _page(values: list[Any]) -> tuple[list[Any], int]:
    return values[:LIST_CAP], max(len(values) - LIST_CAP, 0)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stuck_ms(ctx: Context) -> int:
    hours = ctx.pack.policy.get("stuck_hours", 0)
    return int(float(hours) * 60 * 60 * 1000)
