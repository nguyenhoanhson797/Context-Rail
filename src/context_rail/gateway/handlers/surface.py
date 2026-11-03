"""Read-only surface commands: types, next, explain and why."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.context import surface
from context_rail.kernel import structure
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["explain", "next", "types", "why"]

_QUEUE_CARDS = 10


def types(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The catalog, scoped to the caller's role."""
    role = _role(ctx.actor)
    return surface.catalog(
        ctx, role=role, item=args.get("item"), limit=args["limit"]
    )


def next(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The CTO's executable queue, or one worker's card."""
    role = _role(ctx.actor)
    if role != "cto":
        if not args.get("item"):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "a worker needs --item to read its next card",
                {"actor": ctx.actor},
            )
        return surface.card(ctx, args["item"])
    from context_rail.context import recipes

    rows = recipes.item_rows(ctx.index, ctx.pack)
    runnable = structure.executable(rows)
    cards = [
        surface.card(ctx, slug)
        for slug in runnable[:_QUEUE_CARDS]
    ]
    return {
        "queue": runnable,
        "cards": cards,
        "omitted": max(len(runnable) - _QUEUE_CARDS, 0),
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
