"""Read-only surface commands: types, next, model, explain and why."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.context import operating, surface
from context_rail.gateway.handlers.common import read_filter, read_refusal
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["explain", "model", "next", "types", "why"]

_QUEUE_CARDS = 10
_QUEUE_CAP = 50
_HIERARCHY_CAP = 20


def types(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The catalog, scoped to the caller's role or seat."""
    identity = ctx.identity
    if ctx.role_mode and identity.kind == "seat" and identity.role is not None:
        return surface.catalog(
            ctx,
            lead=False,
            presets=_role_presets(identity.role),
            item=args.get("item"),
            limit=args["limit"],
        )
    return surface.catalog(
        ctx, lead=identity.lead, item=args.get("item"), limit=args["limit"]
    )


def _role_presets(role: Any) -> frozenset[str]:
    names: set[str] = set(role.anchor.presets)
    for value in role.can.methods.values():
        if isinstance(value, (list, tuple)):
            names.update(item for item in value if isinstance(item, str))
        elif isinstance(value, dict):
            names.update(key for key in value if isinstance(key, str))
    return frozenset(names)


def next(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The operating queue, or one worker's card with its focus breadcrumb."""
    identity = ctx.identity
    if (
        ctx.role_mode
        and identity.kind == "seat"
        and identity.role is not None
        and identity.seat is not None
    ):
        return _seat_next(ctx, args, identity)
    if not identity.lead:
        if not args.get("item"):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "a worker needs --item to read its next card",
                {"actor": ctx.actor},
            )
        refused = read_refusal(ctx, args["item"])
        if refused is not None:
            raise refused
        return surface.card(ctx, args["item"])
    try:
        view = operating.resolve_queue(
            ctx,
            ctx.model,
            ctx.actor,
            all=bool(args.get("all")),
            into=args.get("into"),
            focus_only=bool(args.get("focus_only")),
        )
    except operating.OperatingError as error:
        raise WriteRefused(error.code, error.message, error.details) from None
    if args.get("brief"):
        return {
            "queue": [item.slug for item in view.items],
            "blocked": view.blocked,
            "omitted": 0,
            "focus": view.focus.as_dict(),
            "scope": view.scope,
        }
    cards = [surface.card(ctx, item.slug) for item in view.items[:_QUEUE_CARDS]]
    return {
        "queue": [item.slug for item in view.items],
        "items": [item.as_dict() for item in view.items],
        "blocked": view.blocked,
        "cards": cards,
        "omitted": max(len(view.items) - _QUEUE_CARDS, 0),
        "focus": view.focus.as_dict(),
        "scope": view.scope,
    }


def _seat_next(
    ctx: Context, args: dict[str, Any], identity: Any
) -> dict[str, Any]:
    """A seat's next: its anchor's card, or the queue inside its anchor.

    A lens reads as the seat it shows, so the queue and card come from that
    seat's anchor, not the caller's.
    """
    seat = identity.view_as or identity.seat
    role = ctx.pack.operating.roles.get(seat.role) or identity.role
    anchor = seat.anchor
    if args.get("item"):
        refused = read_refusal(ctx, args["item"])
        if refused is not None:
            raise refused
    if role.anchor.kind == "item":
        slug = args.get("item") or anchor
        refused = read_refusal(ctx, slug)
        if refused is not None:
            raise refused
        return surface.card(ctx, slug)
    into = args.get("into") or (None if role.anchor.kind == "project" else anchor)
    if into is not None:
        refused = read_refusal(ctx, into)
        if refused is not None:
            raise refused
    try:
        view = operating.resolve_queue(
            ctx,
            ctx.model,
            ctx.actor,
            all=bool(args.get("all")),
            into=into,
            focus_only=bool(args.get("focus_only")),
        )
    except operating.OperatingError as error:
        raise WriteRefused(error.code, error.message, error.details) from None
    items = [item for item in view.items if _readable(ctx, item.slug)]
    blocked = {
        slug: reason for slug, reason in view.blocked.items() if _readable(ctx, slug)
    }
    queue = [item.slug for item in items]
    return {
        "queue": queue[:_QUEUE_CAP],
        "items": [item.as_dict() for item in items[:_QUEUE_CAP]],
        "blocked": dict(list(blocked.items())[:_QUEUE_CAP]),
        "focus": view.focus.as_dict(),
        "scope": view.scope,
        "omitted": {
            "queue": max(len(queue) - _QUEUE_CAP, 0),
            "items": max(len(items) - _QUEUE_CAP, 0),
            "blocked": max(len(blocked) - _QUEUE_CAP, 0),
        },
    }


def _readable(ctx: Context, slug: str) -> bool:
    return read_refusal(ctx, slug) is None


def model(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The compiled IR, the resolved focus, unit and hierarchy, and digests."""
    compiled = ctx.model
    unit = operating.resolve_unit(ctx, compiled)
    try:
        focus = operating.resolve_focus(ctx, compiled).as_dict()
    except operating.OperatingError as error:
        focus = {
            "state": "ambiguous" if error.code == "AMBIGUOUS_FOCUS" else "invalid",
            "focus": None,
            "candidates": error.details.get("candidates", []),
            "resolved_by": "refused",
            "error": error.code,
        }
    return {
        "model": compiled.as_dict(),
        "unit": None if unit is None else unit.as_dict(),
        "focus": focus,
        "hierarchy": _hierarchy_page(ctx, compiled),
        "digests": {
            "semantic": compiled.semantic_digest,
            "presentation": compiled.presentation_digest,
            "full": compiled.full_digest,
        },
        "neutral": ctx.pack.operating is None,
    }


def _hierarchy_page(ctx: Context, model: Any) -> list[dict[str, Any]]:
    page: list[dict[str, Any]] = []
    for group in operating.resolve_hierarchy(ctx, model):
        value = group.as_dict()
        kept, outside = read_filter(ctx, [item["slug"] for item in value["items"]])
        if outside:
            visible = set(kept)
            value["items"] = [
                item for item in value["items"] if item["slug"] in visible
            ]
        value["omitted"] = max(len(value["items"]) - _HIERARCHY_CAP, 0)
        value["items"] = value["items"][:_HIERARCHY_CAP]
        value["omitted_outside_reach"] = outside
        page.append(value)
    return page


def explain(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The full preset, workflow or action definition."""
    try:
        return surface.explain(ctx, args["name"], limit=args["limit"])
    except surface.SurfaceError as error:
        raise WriteRefused(error.code, error.message, error.details) from None


def why(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The proof one item still needs, and the command that produces it."""
    from context_rail.gateway.handlers.common import read_refusal

    refused = read_refusal(ctx, args["slug"])
    if refused is not None:
        raise refused
    try:
        return surface.why(ctx, args["slug"])
    except surface.SurfaceError as error:
        raise WriteRefused(error.code, error.message, error.details) from None
