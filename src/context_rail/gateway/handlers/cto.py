"""The project brief every caller reads first."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["brief"]


def brief(context: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The bounded CTO brief, or a seat's own brief when its anchor is scoped."""
    from context_rail.context import recipes

    identity = context.identity
    if (
        context.role_mode
        and identity.kind == "seat"
        and identity.role is not None
        and identity.seat is not None
        and identity.role.anchor.kind != "project"
    ):
        from context_rail.context.seat_brief import seat_brief

        return seat_brief(
            context, identity.seat, identity.role, max_chars=args["max_chars"]
        )
    return recipes.brief(
        context, max_chars=args["max_chars"], item_limit=args["item_limit"]
    )
