"""``rail ops codegraph``: check the provider, build a snapshot, show its state.

``setup``, ``refresh`` and ``status`` read the store and write only the
rebuildable cache under the local directory; none starts an outside tool.
``adopt`` is the one command that writes store documents: it moves a store's
profiles and skills off the retired hand-written code map.
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
from context_rail.store.codemap import pins_code_map, semantic_fingerprint

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["adopt", "adopt_fingerprint", "probe_only", "refresh", "resolved", "setup", "status"]

_SOURCE_WORDS = {"rail.yaml": "rail.yaml", "pack": "pack default", "default": "default"}
_ADOPT_REASON = "adopt the code graph"
_ADOPT_HINT = (
    "the implementer profile still pins the retired code-map skill; "
    "run `rail ops codegraph adopt`"
)


def adopt_fingerprint(text: str) -> str:
    """The hash `adopt` compares: what a profile or skill says, not how it is laid out."""
    return semantic_fingerprint(text)


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


def probe_only(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Check an external provider and cache readiness, without building a snapshot."""
    from context_rail.runner.codegraph.probe import probe

    configured, _source = resolved(ctx.manifest, ctx.pack)
    store = Path(ctx.store)
    if configured in ("graphify", "gitnexus"):
        found = worktree_git_dir(store)
        head = head_sha(store)
        if found is None or head is None:
            return _hinted(ctx, _not_git(ctx))
        probe(store, found[1], configured)
    return _hinted(ctx, _status(ctx))


def setup(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Check the provider, then build the snapshot of the checkout's HEAD."""
    from context_rail.runner.codegraph.probe import probe

    configured, _source = resolved(ctx.manifest, ctx.pack)
    store = Path(ctx.store)
    if configured in ("graphify", "gitnexus"):
        found = worktree_git_dir(store)
        head = head_sha(store)
        if found is None or head is None:
            return _hinted(ctx, _not_git(ctx))
        record = probe(store, found[1], configured)
        if not record.get("ready", False):
            return _hinted(ctx, _status(ctx))
        return _hinted(ctx, _build(ctx, force=True))
    return _hinted(ctx, _build(ctx))


def refresh(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Build the snapshot of the checkout's current HEAD when it has none."""
    return _hinted(ctx, _build(ctx))


def _hinted(ctx: Context, value: dict[str, Any]) -> dict[str, Any]:
    if value["configured"] != "none" and pins_code_map(Path(ctx.store), ctx.manifest):
        value["hint"] = _ADOPT_HINT
    return value


def adopt(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Rewrite the unmodified profiles and skills of the old code map, keep the rest.

    A copy the store changed by hand is never overwritten; its difference from
    the pack is reported instead. A second run finds nothing left to do.
    """
    from context_rail.gateway.handlers.shipped import (
        commit_shipped,
        plan_shipped,
        shipped_fingerprints,
    )
    from context_rail.store.lock import store_lock

    store = Path(ctx.store)
    dry_run = bool(args.get("dry_run"))
    report: dict[str, Any] = {
        "adopted": [],
        "removed": [],
        "already": [],
        "customized": [],
        "dry_run": dry_run,
    }
    if not shipped_fingerprints(ctx.pack.name):
        report["line"] = "nothing to adopt: this pack never shipped the hand-written code map"
        return report
    if dry_run:
        plan = plan_shipped(ctx, reason=_ADOPT_REASON, seed=False)
    else:
        with store_lock(store):
            plan = plan_shipped(ctx, reason=_ADOPT_REASON, seed=False)
            commit_shipped(ctx, plan, actor=actor)
    report["adopted"] = plan.adopted
    report["removed"] = plan.removed
    report["already"] = plan.already
    report["customized"] = plan.customized
    report["line"] = _adopt_line(report)
    return report


def _adopt_line(report: dict[str, Any]) -> str:
    parts = [f"{len(report[name])} {name}" for name in ("adopted", "removed", "already", "customized")]
    return "code map: " + ", ".join(parts)


def _build(ctx: Context, *, force: bool = False) -> dict[str, Any]:
    from context_rail.runner.codegraph import ensure_snapshot

    configured, _source = resolved(ctx.manifest, ctx.pack)
    store = Path(ctx.store)
    if configured == "none":
        return _status(ctx)
    found = worktree_git_dir(store)
    head = head_sha(store)
    if found is None or head is None:
        return _not_git(ctx)
    ensured = ensure_snapshot(
        store,
        worktree=found[1],
        commit=head,
        configured=configured,
        force=force or configured in ("graphify", "gitnexus"),
    )
    value = _status(ctx)
    value["built"] = ensured.built
    if ensured.provider is None:
        value["ready"] = False
        if ensured.warning is not None:
            value["line"] = f"code graph: not built: {ensured.warning}"
    return value


def _not_git(ctx: Context) -> dict[str, Any]:
    value = _status(ctx)
    value["ready"] = False
    value["line"] = "code graph: not ready: git: the store is not inside a git checkout"
    return value


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
    event = last_event(store)
    if configured == "builtin" and info is None and ready.get("ready"):
        detail = {} if event is None else event.get("detail") or {}
        message = (
            detail.get("message")
            if event is not None and event.get("kind") in ("timeout", "unavailable")
            else "no snapshot yet"
        )
        ready = {
            **ready,
            "ready": False,
            "step": ready.get("step") or "snapshot",
            "fix": ready.get("fix") or "rail ops codegraph setup",
            "line": f"code graph: not built: {message}",
        }
    provider = None if info is None else info["provider"]
    if provider is None and configured in ("graphify", "gitnexus") and ready.get("ready"):
        provider = configured
    return {
        "configured": configured,
        "source": source,
        "ready": ready["ready"],
        "provider": provider,
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
        "last_event": event,
        "line": ready["line"],
    }
