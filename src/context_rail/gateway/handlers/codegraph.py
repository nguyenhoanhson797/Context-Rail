"""``rail ops codegraph``: check the provider, build a snapshot, show its state.

``setup``, ``refresh`` and ``status`` read the store and write only the
rebuildable cache under the local directory; none starts an outside tool.
``adopt`` is the one command that writes store documents: it moves a store's
profiles and skills off the retired hand-written code map.
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.locate import head_sha, worktree_git_dir
from context_rail.core.util import now_iso
from context_rail.model.codegraph import code_graph_source, effective_code_graph
from context_rail.store.codegraph import (
    freshness,
    last_event,
    snapshot_info,
)
from context_rail.store.codemap import pins_code_map, semantic_fingerprint

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["adopt", "adopt_fingerprint", "refresh", "resolved", "setup", "status"]

_SOURCE_WORDS = {"rail.yaml": "rail.yaml", "pack": "pack default", "default": "default"}
_DIFF_LINES = 80
_RETIRED_NOTE = "retired in this pack; delete it by hand once nothing relies on it"
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


def setup(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Check the provider, then build the snapshot of the checkout's HEAD."""
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
    from context_rail.gateway.handlers.common import (
        compose_doc,
        doc_frontmatter,
        document_version,
        read_doc,
        versioned_change_record,
        versioned_kind,
    )
    from context_rail.gateway.handlers.onboarding import agent_ops
    from context_rail.store.doc import etag, parse_doc
    from context_rail.store.lock import store_lock
    from context_rail.store.write import Op, commit

    store = Path(ctx.store)
    dry_run = bool(args.get("dry_run"))
    known = _adoptable().get(ctx.pack.name, {})
    report: dict[str, Any] = {
        "adopted": [],
        "removed": [],
        "already": [],
        "customized": [],
        "dry_run": dry_run,
    }
    if not known:
        report["line"] = "nothing to adopt: this pack never shipped the hand-written code map"
        return report
    current = {
        op.path: op.data.decode("utf-8")
        for op in agent_ops(store, ctx.manifest, ctx.pack.name)
        if op.data is not None
    }
    with store_lock(store):
        ops: list[Op] = []
        records: list[dict[str, Any]] = []
        for path in sorted(known):
            target = store / path
            if not target.is_file():
                continue
            raw = target.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            fingerprint = adopt_fingerprint(text)
            wanted = current.get(path)
            if wanted is not None and fingerprint == adopt_fingerprint(wanted):
                report["already"].append(path)
                continue
            if fingerprint not in known[path]:
                report["customized"].append(
                    {"path": path, "diff": _diff(text, wanted)}
                )
                continue
            actual = etag(raw)
            if wanted is None:
                ops.append(Op(path, None, actual))
                report["removed"].append(path)
                continue
            _data, doc = read_doc(store, path)
            front = doc_frontmatter(parse_doc(path, wanted.encode("utf-8")))
            before = document_version(doc_frontmatter(doc), path)
            front["version"] = before + 1
            created = doc_frontmatter(doc).get("created")
            if created is not None:
                front["created"] = created
            front["updated"] = now_iso()
            after = compose_doc(front, parse_doc(path, wanted.encode("utf-8")).body)
            kind = versioned_kind(doc, path)
            ops.append(Op(path, after, actual))
            report["adopted"].append(path)
            if kind is not None:
                records.append(
                    versioned_change_record(
                        kind=kind,
                        rel=path,
                        slug=doc.slug,
                        before_version=before,
                        after_version=before + 1,
                        before_text=text,
                        after_text=after.decode("utf-8"),
                        before_etag=actual,
                        after_etag=etag(after),
                        reason="adopt the code graph",
                    )
                )
        if ops and not dry_run:
            commit(
                store,
                ctx.manifest,
                ops,
                records[0] if records else None,
                actor=actor,
                index=ctx.index,
                extra_records=tuple(records[1:]),
            )
    report["line"] = _adopt_line(report)
    return report


def _adopt_line(report: dict[str, Any]) -> str:
    parts = [f"{len(report[name])} {name}" for name in ("adopted", "removed", "already", "customized")]
    return "code map: " + ", ".join(parts)


def _diff(current: str, wanted: str | None) -> str:
    if wanted is None:
        return _RETIRED_NOTE
    lines = list(
        difflib.unified_diff(
            current.replace("\r\n", "\n").splitlines(),
            wanted.splitlines(),
            "store",
            "pack",
            lineterm="",
        )
    )
    if len(lines) > _DIFF_LINES:
        lines = [*lines[:_DIFF_LINES], f"... {len(lines) - _DIFF_LINES} more lines"]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _adoptable() -> dict[str, dict[str, list[str]]]:
    """Per pack, the fingerprints of every profile and skill the old code map shipped."""
    source = resources.files("context_rail").joinpath("packs", "adoptable.json")
    return json.loads(source.read_text(encoding="utf-8"))


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
