"""The loopback RPC daemon: one warm process for one store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.gateway.daemon import serve as serve_store
from context_rail.gateway.daemon import stop_daemon
from context_rail.gateway.gateway import last_timing

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["serve"]


def serve(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Block serving this store until stopped or idle, or stop the one running."""
    if args.get("stop"):
        return {"stopped": stop_daemon(ctx.store)}
    idle = args.get("idle")
    serve_store(
        ctx.store,
        port=int(args["port"]),
        idle_s=int(idle) if idle is not None else None,
    )
    return {"served": True, "perf": last_timing()}
