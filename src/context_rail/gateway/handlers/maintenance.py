"""Maintenance tools: reindex."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from context_rail.store.index import Index

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def reindex(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Sync the index, or rebuild it with ``--full``, and report the counts."""
    index = Index.open(ctx.store, ctx.pack)
    try:
        stats = index.rebuild() if args["full"] else index.sync()
    finally:
        index.close()
    return asdict(stats)
