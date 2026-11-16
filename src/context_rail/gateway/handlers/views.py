"""Read views: find, show, the status picture, the ledger and the digest."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from context_rail.context import operating, recipes
from context_rail.gateway.confirmation import confirmation_phrase
from context_rail.gateway.handlers.common import parse_relation_filter, read_refusal
from context_rail.kernel import structure
from context_rail.model.primitives import CATEGORIES
from context_rail.store import approval, query
from context_rail.store.write import resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

LIST_CAP = 20
HISTORY_CAP = 10
PACK_PREFIX = "pack:"
CHARTER_TARGET = "charter"


def find(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Page through the documents matching every filter, inside the read reach."""
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
        allow=_allow(ctx),
    )


def _allow(ctx: Context) -> Any:
    reach = ctx.reach
    if reach is None:
        return None
    return lambda slug, path, preset: reach.can_read(
        slug, path=path, preset=preset
    )


def check_list(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Page through Check documents, never every primitive."""
    reach = ctx.reach
    if reach is None:
        allow = None
    else:
        def allow(slug: str, path: str, preset: str | None) -> bool:
            if preset != "check":
                return reach.can_read(slug, path=path, preset=preset)
            row = ctx.index.conn.execute(
                "SELECT dst FROM relations WHERE src_path = ? AND rel = 'verifies' "
                "LIMIT 1",
                (path,),
            ).fetchone()
            return row is not None and _readable(ctx, row[0])

    return query.find(
        ctx.index,
        primitive="check",
        tags=tuple(args["tag"]),
        status=args["status"],
        text=args["text"],
        limit=args["limit"],
        allow=allow,
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
    from context_rail.context import code_targets

    if code_targets.looks_like_code_target(slug):
        return code_targets.code_view(ctx, slug)
    if not args.get("path"):
        resolve_unique(index, args["slug"])
    refused = read_refusal(ctx, args["slug"], path=args.get("path"))
    if refused is not None:
        raise refused
    try:
        shown = query.show(
            index,
            args["slug"],
            path=args.get("path"),
            body_chars=args["body_chars"],
        )
    except FileNotFoundError:
        # A document can vanish between the index sync and the body read.
        raise LookupError("not_found") from None
    doc = shown["doc"]
    if ctx.reach is not None:
        for side in ("out", "in"):
            shown[side] = {
                rel: [slug for slug in slugs if _readable(ctx, slug)]
                for rel, slugs in (shown.get(side) or {}).items()
            }
    if doc["primitive"] == "item":
        from context_rail.context import results as result_context_mod
        from context_rail.context import surface

        try:
            shown["card"] = surface.card(ctx, doc["slug"])
        except surface.SurfaceError:
            pass
        compiled_res = result_context_mod.compiled_results_for_item(ctx, doc["slug"])
        res_info = result_context_mod.result_context(ctx, doc["slug"], include_body=False)
        if compiled_res is not None or res_info.get("selected") is not None:
            shown["results"] = {
                "selected": res_info.get("selected"),
                "current": res_info.get("current"),
                "completed": res_info.get("completed"),
                "reasons": list(res_info.get("reasons") or ()),
                "review": res_info.get("review"),
                "history": res_info.get("history") or [],
                "history_omitted": res_info.get("history_omitted", 0),
                "contract": (
                    None
                    if compiled_res is None
                    else {
                        "produce": result_context_mod.compiled_artifact_dict(
                            compiled_res.produce
                        ),
                        "review": result_context_mod.compiled_artifact_dict(
                            compiled_res.review
                        ),
                    }
                ),
            }
        shown["checks"] = [
            {
                "id": row.get("slug"),
                "item": row.get("item"),
                "passed": row.get("passed"),
                "freshness": recipes.freshness(ctx, row),
            }
            for row in query.check_states(index, doc["slug"])
        ]
    if doc["primitive"] == "check":
        shown["freshness"] = recipes.freshness(
            ctx, query.check_state(index, doc["slug"])
        )
    history = query.records_about(index, doc["slug"], limit=HISTORY_CAP + 1)
    shown["history"] = [
        {
            "slug": row.get("slug") or row.get("id"),
            "kind": row.get("kind"),
            "at": row.get("at"),
            "actor": row.get("actor"),
        }
        for row in history[:HISTORY_CAP]
    ]
    shown["history_omitted"] = max(len(history) - HISTORY_CAP, 0)
    if doc["primitive"] == "policy" and doc["preset"] == CHARTER_TARGET:
        shown["approval"] = approval.approval_state(ctx, CHARTER_TARGET)
        shown["phrase"] = confirmation_phrase("approve", CHARTER_TARGET)
    return shown


def status(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Counts, executable items, blockers, stuck and too-long items, each capped."""
    index = ctx.index
    all_items = recipes.item_rows(index, ctx.pack)
    items = (
        all_items
        if ctx.reach is None
        else recipes.item_rows(index, ctx.pack, reach=ctx.reach)
    )
    titles = recipes.item_titles(index)

    counts = {category: 0 for category in CATEGORIES}
    for item in items:
        if item.category in counts:
            counts[item.category] += 1

    held_now = structure.blocked(all_items)
    runnable = structure.executable(items, held_now)
    visible = {item.slug for item in items}
    finished = {
        item.slug
        for item in all_items
        if item.category in structure.FINISHED_CATEGORIES
    }
    held = {
        slug: operating.held_reason(ctx, reason)
        for slug, reason in held_now.items()
        if slug in visible and slug not in finished
    }
    stalled = [
        row["slug"]
        for row in recipes.stuck_rows(
            items, _now_ms(), recipes.stuck_window_ms(ctx), titles
        )
    ]
    long_items = [row["slug"] for row in recipes.too_long_rows(ctx, items, titles)]

    executable_page, executable_omitted = _page(runnable)
    blocked_page, blocked_omitted = _page(sorted(held))
    stuck_page, stuck_omitted = _page(stalled)
    too_long_page, too_long_omitted = _page(long_items)
    checks = query.check_states(index)
    if ctx.reach is not None:
        visible = {item.slug for item in items}
        checks = [row for row in checks if row.get("item") in visible]
    states = [
        {
            "check": row["slug"],
            "item": row["item"],
            "passed": row["passed"],
            "freshness": recipes.freshness(ctx, row),
        }
        for row in checks[:LIST_CAP]
    ]
    model = ctx.model
    unit = operating.resolve_unit(ctx, model)
    preset_rows: dict[str, list[operating.ItemRow]] = {}
    parents = operating._parents(ctx)
    groups, groups_omitted = _groups(ctx, model, preset_rows=preset_rows)
    focus = _focus(ctx, model, preset_rows=preset_rows)
    progress_data = operating.resolve_progress(
        ctx, model, parents=parents, preset_rows=preset_rows
    ).as_dict()
    progress_children = list(progress_data.get("children") or ())
    progress_data["children"] = progress_children[:LIST_CAP]
    progress_data["omitted"] = int(progress_data.get("omitted") or 0) + max(
        len(progress_children) - LIST_CAP, 0
    )
    queue = _queue(
        ctx,
        model,
        all_items=all_items,
        held_map=held_now,
        parents=parents,
        preset_rows=preset_rows,
    )
    result = {
        "counts": counts,
        "counts_scope": "every item preset" if ctx.reach is None else "read reach",
        "executable": executable_page,
        "blocked": {slug: held[slug] for slug in blocked_page},
        "stuck": stuck_page,
        "too_long": too_long_page,
        "check_states": states,
        "check_states_omitted": max(len(checks) - LIST_CAP, 0),
        "approval": recipes.approval_state(ctx),
        "unit": None if unit is None else unit.as_dict(),
        "focus": focus,
        "groups": groups,
        "progress": progress_data,
        "queue": queue,
        "labels": {
            name: {"one": operating.label(model, name), "many": operating.label(model, name, plural=True)}
            for name in model.presentation.labels
        },
        "groups_omitted": groups_omitted,
        "omitted": {
            "executable": executable_omitted,
            "blocked": blocked_omitted,
            "stuck": stuck_omitted,
            "too_long": too_long_omitted,
        },
    }
    if ctx.reach is not None:
        result["omitted_outside_reach"] = len(all_items) - len(items)
    return result


def _focus(
    ctx: Context,
    model: Any,
    *,
    preset_rows: dict[str, list[operating.ItemRow]] | None = None,
) -> dict[str, Any]:
    try:
        return operating.resolve_focus(ctx, model, preset_rows=preset_rows).as_dict()
    except operating.OperatingError as error:
        return {
            "state": "ambiguous" if error.code == "AMBIGUOUS_FOCUS" else "invalid",
            "focus": None,
            "candidates": error.details.get("candidates", []),
            "resolved_by": "refused",
            "error": error.code,
        }


def _queue(
    ctx: Context,
    model: Any,
    *,
    all_items: Any = None,
    held_map: Any = None,
    parents: Any = None,
    preset_rows: dict[str, list[operating.ItemRow]] | None = None,
) -> dict[str, Any]:
    try:
        view = operating.resolve_queue(
            ctx,
            model,
            ctx.actor,
            all_items=all_items,
            held_map=held_map,
            parents=parents,
            preset_rows=preset_rows,
        )
    except operating.OperatingError as error:
        return {
            "items": [],
            "omitted": 0,
            "scope": "project",
            "error": error.code,
        }
    items = [item.as_dict() for item in view.items]
    return {
        "items": items[:LIST_CAP],
        "blocked": dict(view.blocked),
        "omitted": max(len(items) - LIST_CAP, 0),
        "scope": view.scope,
    }


def _groups(
    ctx: Context,
    model: Any,
    *,
    preset_rows: dict[str, list[operating.ItemRow]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    groups = operating.resolve_hierarchy(ctx, model, preset_rows=preset_rows)
    shown: list[dict[str, Any]] = []
    omitted = 0
    for group in groups:
        values = group.as_dict()
        if ctx.reach is not None and not values["items"]:
            continue
        values["items"] = values["items"][:LIST_CAP]
        omitted += max(len(group.items) - LIST_CAP, 0)
        shown.append(values)
    return shown, omitted


def _readable(ctx: Context, slug: Any) -> bool:
    if ctx.reach is None:
        return True
    if not isinstance(slug, str) or not slug:
        return False
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1", (slug,)
    ).fetchone()
    if row is None:
        return False
    return ctx.reach.can_read(slug, path=row[0], preset=row[1])


def ledger(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Candidates with the usage they reported, unknown counters kept apart."""
    result = query.ledger(
        ctx.index,
        item=args["item"],
        profile=args["profile"],
        since=args["since"],
        limit=args["limit"],
    )
    reach = ctx.reach
    if reach is None:
        return result
    listed = result["candidates"]
    kept = [row for row in listed if _item_in_reach(ctx, reach, row.get("item"))]
    result["candidates"] = kept
    result["totals"] = query.totals_for_candidates(
        ctx.index, [row["record_id"] for row in kept]
    )
    unknown = result["totals"]["unknown_attempts"]
    result["unknown_counts"] = unknown
    result["omitted_outside_reach"] = len(listed) - len(kept)
    return result


def _item_in_reach(ctx: Context, reach: Any, slug: Any) -> bool:
    if not isinstance(slug, str) or not slug:
        return reach.read_all
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1", (slug,)
    ).fetchone()
    if row is None:
        return False
    return reach.can_read(slug, path=row[0], preset=row[1])


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
