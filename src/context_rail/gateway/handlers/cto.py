"""The CTO's own reads: the bounded brief and, from Task 16, the tick session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def brief(context: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The bounded CTO brief, rendered from indexed projections only."""
    from context_rail.context import recipes

    return recipes.brief(
        context, max_chars=args["max_chars"], item_limit=args["item_limit"]
    )
