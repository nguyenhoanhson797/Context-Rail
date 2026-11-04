"""Read-only surface commands: types, next, model, explain and why."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.context import operating, surface
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["explain", "model", "next", "types", "why"]

_QUEUE_CARDS = 10


def types(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The catalog, scoped to the caller's role."""
    role = _role(ctx.actor)
    return surface.catalog(
        ctx, role=role, item=args.get("item"), limit=args["limit"]
    )


def next(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The operating queue, or one worker's card with its focus breadcrumb."""
    role = _role(ctx.actor)
    if role != "cto":
        if not args.get("item"):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "a worker needs --item to read its next card",
                {"actor": ctx.actor},
            )
        return surface.card(ctx, args["item"])
    try:
        view = operating.resolve_queue(
            ctx,
            ctx.model,
            ctx.actor,
            all_items=bool(args.get("all")),
            into=args.get("into"),
            focus_only=bool(args.get("focus_only")),
        )
    except operating.OperatingError as error:
        raise WriteRefused(error.code, error.message, error.details) from None
    cards = [surface.card(ctx, item.slug) for item in view.items[:_QUEUE_CARDS]]
    return {
        "queue": [item.slug for item in view.items],
        "items": [item.as_dict() for item in view.items],
        "cards": cards,
        "omitted": max(len(view.items) - _QUEUE_CARDS, 0),
        "focus": view.focus.as_dict(),
        "scope": view.scope,
    }


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
        "hierarchy": [
            group.as_dict() for group in operating.resolve_hierarchy(ctx, compiled)
        ],
        "digests": {
            "semantic": compiled.semantic_digest,
            "presentation": compiled.presentation_digest,
            "full": compiled.full_digest,
        },
        "neutral": ctx.pack.operating is None,
    }


def explain(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The full preset, workflow or action definition."""
    try:
        return surface.explain(ctx, args["name"], limit=args["limit"])
    except surface.SurfaceError as error:
        raise WriteRefused(error.code, error.message, error.details) from None


def why(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The proof one item still needs, and the command that produces it."""
    try:
        return surface.why(ctx, args["slug"])
    except surface.SurfaceError as error:
        raise WriteRefused(error.code, error.message, error.details) from None


def _role(actor: str) -> str:
    return "cto" if actor in ("cto", "human") else actor
