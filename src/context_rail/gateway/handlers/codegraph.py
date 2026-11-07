"""``rail ops codegraph``: check the provider, build a snapshot, show its state.

All three read the store and write only the rebuildable cache under the local
directory. They never touch a store document, and none starts an outside tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.locate import head_sha, worktree_git_dir
from context_rail.model.codegraph import code_graph_source, effective_code_graph
from context_rail.store.codegraph import (
    freshness,
    last_event,
    snapshot_info,
)

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["refresh", "resolved", "setup", "status"]

_SOURCE_WORDS = {"rail.yaml": "rail.yaml", "pack": "pack default", "default": "default"}


def resolved(manifest: Any, pack: Any) -> tuple[str, str]:
    """``(provider choice, where it came from)`` for this store."""
    default = pack.policy.get("code_graph")
    return (
        effective_code_graph(manifest.code_graph, default),
        code_graph_source(manifest.code_graph, default),
    )


def status(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The provider, its newest snapshot, coverage and freshness. Builds nothing."""
    return _status(ctx)


def setup(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Check the provider, then build the snapshot of the checkout's HEAD."""
    return _build(ctx)


def refresh(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Build the snapshot of the checkout's current HEAD when it has none."""
    return _build(ctx)


def _build(ctx: Context) -> dict[str, Any]:
    from context_rail.runner.codegraph import ensure_snapshot

    configured, _source = resolved(ctx.manifest, ctx.pack)
    store = Path(ctx.store)
    if configured != "none":
        found = worktree_git_dir(store)
        head = head_sha(store)
        if found is None or head is None:
            value = _status(ctx)
            value["ready"] = False
            value["line"] = "code graph: not ready: git: the store is not inside a git checkout"
            return value
        ensure_snapshot(store, worktree=found[1], commit=head, configured=configured)
    return _status(ctx)


def _status(ctx: Context) -> dict[str, Any]:
    from context_rail.runner.codegraph import readiness

    configured, source = resolved(ctx.manifest, ctx.pack)
    store = Path(ctx.store)
    if configured == "none":
        return {
            "configured": "none",
            "source": source,
            "ready": False,
            "provider": None,
            "commit": None,
            "freshness": {"state": "none", "graph": None, "head": None},
            "coverage": None,
            "last_event": None,
            "line": f"code graph: off ({_SOURCE_WORDS[source]})",
        }
    state = freshness(store)
    info = None if state["graph"] is None else snapshot_info(store, state["graph"])
    ready = readiness(store, configured)
    return {
        "configured": configured,
        "source": source,
        "ready": ready["ready"],
        "provider": None if info is None else info["provider"],
        "commit": state["graph"],
        "freshness": state,
        "coverage": None
        if info is None
        else {
            "files_listed": info["files_listed"],
            "files_parsed": info["files_parsed"],
            "skipped": info["skipped"],
            "nodes": info["nodes"],
            "edges": info["edges"],
        },
        "last_event": last_event(store),
        "line": ready["line"],
    }
