"""The loopback RPC daemon: one store, one Index, one ProjectModel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.gateway.daemon import serve as serve_store
from context_rail.gateway.gateway import last_timing

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["serve"]


def serve(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Block serving this store over loopback until interrupted."""
    port = int(args["port"])
    serve_store(ctx.store, port=port)
    return {"served": True, "perf": last_timing()}
