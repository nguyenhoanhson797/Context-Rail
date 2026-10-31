"""Read views: find, show, the status picture, the ledger and the digest."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from context_rail.context import recipes
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
    try:
        shown = query.show(index, args["slug"], body_chars=args["body_chars"])
    except FileNotFoundError:
        # A document can vanish between the index sync and the body read.
        raise LookupError("not_found") from None
    doc = shown["doc"]
    if doc["primitive"] == "check":
        shown["freshness"] = recipes.freshness(
            ctx, query.check_state(index, doc["slug"])
        )
    if doc["primitive"] == "policy" and doc["preset"] == CHARTER_TARGET:
        shown["approval"] = approval.approval_state(ctx, CHARTER_TARGET)
        shown["phrase"] = confirmation_phrase("approve", CHARTER_TARGET)
    return shown


def status(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Counts, executable items, blockers, stuck and too-long items, each capped."""
    index = ctx.index
    items = recipes.item_rows(index)
    titles = recipes.item_titles(index)

    counts = {category: 0 for category in CATEGORIES}
    for item in items:
        if item.category in counts:
            counts[item.category] += 1

    runnable = structure.executable(items)
    finished = {
        item.slug for item in items if item.category in structure.FINISHED_CATEGORIES
    }
    held = {
        slug: reason
        for slug, reason in structure.blocked(items).items()
        if slug not in finished
    }
    stalled = [
        row["slug"]
        for row in recipes.stuck_rows(
            items, _now_ms(), recipes.stuck_window_ms(ctx), titles
        )
    ]
    long_items = [row["slug"] for row in recipes.too_long_rows(ctx, items)]

    executable_page, executable_omitted = _page(runnable)
    blocked_page, blocked_omitted = _page(sorted(held))
    stuck_page, stuck_omitted = _page(stalled)
    too_long_page, too_long_omitted = _page(long_items)
    checks = query.check_states(index)
    states = [
        {
            "check": row["slug"],
            "item": row["item"],
            "passed": row["passed"],
            "freshness": recipes.freshness(ctx, row),
        }
        for row in checks[:LIST_CAP]
    ]
    return {
        "counts": counts,
        "executable": executable_page,
        "blocked": {slug: held[slug] for slug in blocked_page},
        "stuck": stuck_page,
        "too_long": too_long_page,
        "check_states": states,
        "check_states_omitted": max(len(checks) - LIST_CAP, 0),
        "approval": recipes.approval_state(ctx),
        "omitted": {
            "executable": executable_omitted,
            "blocked": blocked_omitted,
            "stuck": stuck_omitted,
            "too_long": too_long_omitted,
        },
    }


def ledger(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Runs with their attempt-counted usage, unknown counters kept apart."""
    return query.ledger(
        ctx.index,
        item=args["item"],
        profile=args["profile"],
        since=args["since"],
        limit=args["limit"],
    )


def digest(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The bounded digest of state, waits, decisions, stuck and quality."""
    return recipes.digest(
        ctx,
        since=args["since"],
        limit=args["limit"],
        max_chars=args["max_chars"],
    )


def _page(values: list[Any]) -> tuple[list[Any], int]:
    return values[:LIST_CAP], max(len(values) - LIST_CAP, 0)


def _now_ms() -> int:
    return int(time.time() * 1000)
