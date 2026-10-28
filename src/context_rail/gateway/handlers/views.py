"""Read views: find, show and the status picture, all from the index."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from context_rail.gateway.confirmation import confirmation_phrase
from context_rail.gateway.handlers.common import parse_relation_filter
from context_rail.kernel import structure
from context_rail.model.primitives import CATEGORIES
from context_rail.store import approval, query
from context_rail.store.write import resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

LIST_CAP = 20
PACK_PREFIX = "pack:"
CHARTER_TARGET = "charter"


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
    """One document, its relations and a bounded body, or an approval target."""
    slug = (args["slug"] or "").strip()
    index = ctx.index  # opened and synced before the one body read
    if slug.startswith(PACK_PREFIX):
        return {
            "target": slug,
            "approval": approval.approval_state(ctx, slug),
            "phrase": confirmation_phrase("approve", slug),
        }
    resolve_unique(index, args["slug"])
    shown = query.show(index, args["slug"], body_chars=args["body_chars"])
    doc = shown["doc"]
    if doc["primitive"] == "check":
        shown["freshness"] = _freshness(ctx, query.check_state(index, doc["slug"]))
    if doc["primitive"] == "policy" and doc["preset"] == CHARTER_TARGET:
        shown["approval"] = approval.approval_state(ctx, CHARTER_TARGET)
        shown["phrase"] = confirmation_phrase("approve", CHARTER_TARGET)
    return shown


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
    finished = {
        slug for slug, category in rows if category in structure.FINISHED_CATEGORIES
    }
    held = sorted(
        (slug, reason)
        for slug, reason in structure.blocked(items).items()
        if slug not in finished
    )
    executable_page, executable_omitted = _page(runnable)
    blocked_page, blocked_omitted = _page(held)
    stuck_page, stuck_omitted = _page(stalled)
    checks = query.check_states(index)
    states = [
        {
            "check": row["slug"],
            "item": row["item"],
            "passed": row["passed"],
            "freshness": _freshness(ctx, row),
        }
        for row in checks[:LIST_CAP]
    ]
    return {
        "counts": counts,
        "executable": executable_page,
        "blocked": dict(blocked_page),
        "stuck": stuck_page,
        "check_states": states,
        "check_states_omitted": max(len(checks) - LIST_CAP, 0),
        "omitted": {
            "executable": executable_omitted,
            "blocked": blocked_omitted,
            "stuck": stuck_omitted,
        },
    }


def _freshness(ctx: Context, row: dict[str, Any] | None) -> str:
    from context_rail.gateway.handlers.checks import freshness_of

    return freshness_of(ctx, row)


def _page(values: list[Any]) -> tuple[list[Any], int]:
    return values[:LIST_CAP], max(len(values) - LIST_CAP, 0)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stuck_ms(ctx: Context) -> int:
    hours = ctx.pack.policy.get("stuck_hours", 0)
    return int(float(hours) * 60 * 60 * 1000)
