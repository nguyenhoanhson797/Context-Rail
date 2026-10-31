"""The ``mcp serve`` handler: refuse a nested transport, then run stdio."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.gateway.execution import EXPLICIT_ACTOR, SERVING
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:  # imported lazily by the gateway only when mcp serve runs
    from context_rail.gateway.gateway import Context


def serve(ctx: Context, args: dict[str, Any], actor: str) -> None:
    """Serve one MCP stdio connection, never inside a live connection."""
    if SERVING.get():
        raise WriteRefused(
            "INVALID_STATE",
            "an MCP connection is already serving",
            {"reason": "transport_already_serving"},
        )
    from context_rail.gateway.mcp_server import run_stdio

    run_stdio(ctx.store, EXPLICIT_ACTOR.get())
    return None
