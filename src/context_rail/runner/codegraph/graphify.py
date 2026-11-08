"""The Graphify adapter: one ``graph.json`` in, one schema-1 snapshot out.

Graphify writes its networkx node-link document under the rail's local cache,
not the repository. This module runs the tool, validates the document and maps
its nodes and relations onto the rail's fixed vocabulary.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from context_rail.core.util import now_iso
from context_rail.model.codegraph import Edge, Node, Snapshot
from context_rail.runner.codegraph.provider import (
    ExtractionError,
    command_for,
    resolve_binary,
    version_between,
)
from context_rail.runner.run import run

__all__ = ["extract"]

MAX_SKIPPED_LISTED = 200
_LINE = re.compile(r"^L(\d+)")
_ENV = {
    "GRAPHIFY_BACKEND": "none",
    "GRAPHIFY_NO_AUTO_REFRESH": "1",
    "GRAPHIFY_QUERY_LOG_DISABLE": "1",
    "GRAPHIFY_SKIP_HOOK": "1",
}
_RELATIONS = {
    "calls": "calls",
    "imports": "imports",
    "imports_from": "imports",
    "contains": "contains",
    "method": "contains",
    "inherits": "inherits",
    "implements": "inherits",
    "references": "references",
}
_CONFIDENCE = {"EXTRACTED": "extracted", "INFERRED": "inferred", "AMBIGUOUS": "inferred"}


def extract(
    worktree: Path,
    commit: str,
    *,
    cache_dir: Path,
    timeout_s: float,
) -> Snapshot:
    """Run Graphify in ``worktree`` and return the normalized snapshot."""
    worktree = Path(worktree)
    cache_dir = Path(cache_dir)
    deadline = time.monotonic() + float(timeout_s)
    binary = resolve_binary("graphify", os.environ.get("RAIL_GRAPHIFY_BIN"))
    if binary is None:
        raise ExtractionError(
            "unavailable",
            "graphify is not installed",
            "uv tool install graphifyy  (or: pipx install graphifyy)",
        )
    version = _version(binary, worktree, deadline, cache_dir / "graphify-out")
    graph_path = cache_dir / "graphify-out" / "graph.json"
    command = _command(binary, worktree, cache_dir, graph_path)
    result = _run(command, worktree, deadline, graph_path.parent)
    if result["timed_out"]:
        raise ExtractionError("timeout", "graphify extraction timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError(
            "unavailable",
            "graphify exited " + str(result["exit"]) + ": " + (detail or "no output"),
        )
    return _normalize(_read(graph_path), worktree, commit, version)


def _command(binary: str, worktree: Path, cache_dir: Path, graph_path: Path) -> list[str]:
    prefix = command_for(binary)
    if graph_path.is_file():
        return [*prefix, "update", str(worktree), "--force"]
    return [*prefix, "extract", str(worktree), "--code-only", "--out", str(cache_dir)]


def _version(binary: str, worktree: Path, deadline: float, out_dir: Path) -> str:
    result = _run(
        [*command_for(binary), "--version"],
        worktree,
        deadline,
        out_dir,
    )
    if result["timed_out"]:
        raise ExtractionError("timeout", "graphify version check timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError(
            "unavailable", "graphify version check failed: " + (detail or "no output")
        )
    version = result["stdout"].strip()
    if not version_between(version, "0.9.100", "0.10"):
        raise ExtractionError(
            "unsupported_version",
            f"graphify {version} is outside >=0.9.100,<0.10",
            "install graphifyy >=0.9.100,<0.10",
        )
    return version


def _run(
    command: list[str], worktree: Path, deadline: float, out_dir: Path
) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExtractionError("timeout", "graphify extraction passed its deadline")
    try:
        return run(
            command,
            cwd=worktree,
            confine_to=worktree,
            timeout=remaining,
            extra_env={**_ENV, "GRAPHIFY_OUT": str(out_dir)},
            max_output=4_000_000,
        )
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - the graph is advisory; a broken tool must not stop work
        raise ExtractionError("unavailable", f"graphify could not start: {exc}") from None


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExtractionError("invalid", f"graphify produced no readable graph: {exc}") from None
    if not isinstance(data, dict):
        raise ExtractionError("invalid", "graphify graph.json is not an object")
    graph = data.get("graph")
    if not isinstance(graph, dict) or graph.get("schema_version") != 1:
        raise ExtractionError("invalid", "graphify graph.json has no schema version 1")
    if not isinstance(graph.get("graphify_version"), str):
        raise ExtractionError("invalid", "graphify graph.json has no graphify_version")
    if not isinstance(data.get("nodes"), list) or not isinstance(data.get("links"), list):
        raise ExtractionError("invalid", "graphify graph.json has no nodes or links list")
    return data


def _normalize(
    data: dict[str, Any], worktree: Path, commit: str, version: str
) -> Snapshot:
    raw: dict[str, tuple[str, str, str, int | None]] = {}
    for item in data["nodes"]:
        if not isinstance(item, dict):
            continue
        node_id = item.get("id")
        label = item.get("label")
        source = item.get("source_file")
        if not isinstance(node_id, str) or not isinstance(label, str) or not isinstance(source, str):
            continue
        path = _path(source, worktree)
        if path is None:
            continue
        kind = _kind(item, label, path)
        if kind is None:
            continue
        if node_id in raw:
            continue
        raw[node_id] = (path, kind, label, _start(item.get("source_location")))

    parents: dict[str, str] = {}
    for link in data["links"]:
        if not isinstance(link, dict) or link.get("relation") != "method":
            continue
        source, target = link.get("source"), link.get("target")
        if isinstance(source, str) and isinstance(target, str) and source in raw:
            parents[target] = raw[source][2]

    nodes: dict[str, Node] = {}
    resolved: dict[str, str] = {}
    for graph_id, (path, kind, label, start) in raw.items():
        if kind == "file":
            node = Node(path, "file", Path(path).name, path, None, None)
        elif kind == "class":
            node = Node(f"{path}:{label}", "class", label, path, start, None)
        elif kind == "method":
            name = _method_name(label)
            parent = parents.get(graph_id)
            qualified = f"{parent}.{name}" if parent else name
            node = Node(f"{path}:{qualified}", "method", name, path, start, None)
        else:
            name = _function_name(label)
            node = Node(f"{path}:{name}", "function", name, path, start, None)
        nodes[node.id] = node
        resolved[graph_id] = node.id

    edges: dict[tuple[str, str, str], Edge] = {}
    for link in data["links"]:
        if not isinstance(link, dict):
            continue
        source = resolved.get(link.get("source"))
        target = resolved.get(link.get("target"))
        rel = _RELATIONS.get(str(link.get("relation")))
        confidence = _CONFIDENCE.get(str(link.get("confidence", "EXTRACTED")))
        if source is None or target is None or rel is None or confidence is None:
            continue
        if source == target:
            continue
        edges.setdefault(
            (source, target, rel),
            Edge(source, target, rel, confidence),
        )

    files = {node.file for node in nodes.values()}
    parsed = {node.file for node in nodes.values() if node.kind != "file"}
    listed = sorted(files)
    skipped_paths = sorted(set(listed) - parsed)
    graph_version = str(data["graph"].get("graphify_version") or version)
    return Snapshot(
        provider="graphify",
        provider_version=graph_version,
        commit=commit,
        built_at=now_iso(),
        calls=any(node.kind in ("function", "method") for node in nodes.values()),
        lines=any(node.start is not None for node in nodes.values()),
        files_listed=len(listed),
        files_parsed=len(parsed),
        skipped=tuple(skipped_paths[:MAX_SKIPPED_LISTED]),
        nodes=tuple(sorted(nodes.values(), key=lambda node: node.id)),
        edges=tuple(sorted(edges.values(), key=lambda edge: (edge.src, edge.dst, edge.rel))),
    )


def _kind(item: dict[str, Any], label: str, path: str) -> str | None:
    if item.get("_callable_class"):
        return "class"
    if item.get("_callable"):
        if label.startswith("."):
            return "method"
        if label.endswith("()"):
            return "function"
        return "class"
    if label == Path(path).name or label == path:
        return "file"
    return None


def _method_name(label: str) -> str:
    return label[1:].removesuffix("()")


def _function_name(label: str) -> str:
    return label.removesuffix("()")


def _start(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _LINE.match(value)
    return int(match.group(1)) if match else None


def _path(source: str, worktree: Path) -> str | None:
    text = source.replace("\\", "/").strip()
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            text = candidate.relative_to(worktree).as_posix()
        except ValueError:
            return None
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return text
