"""The ``ui serve`` tool: a loopback UI, never nested inside MCP serving."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.gateway.execution import EXPLICIT_ACTOR, SERVING
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["serve"]


def serve(ctx: Context, args: dict[str, Any], actor: str) -> None:
    """Serve the UI until interrupted; the handler blocks like the transport."""
    if SERVING.get():
        raise WriteRefused(
            "INVALID_STATE",
            "a connection is already serving",
            {"reason": "transport_already_serving"},
        )
    from context_rail.ui.server import serve as run

    run(ctx.store, actor=EXPLICIT_ACTOR.get() or actor, port=args["port"])
    return None
