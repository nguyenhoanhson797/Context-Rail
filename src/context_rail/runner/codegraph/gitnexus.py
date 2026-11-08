"""The GitNexus adapter: index an isolated mirror, then read its graph.

GitNexus 1.6 stores ``.gitnexus`` at the repository root and registers it in a
global home. Both write into the checkout, so the adapter gives it a detached
worktree under the rail's local cache and points ``GITNEXUS_HOME`` there too.
The real worktree never sees the tool's files.
"""

from __future__ import annotations

import json
import os
import shutil
import stat as stat_module
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.model.codegraph import Edge, Node, Snapshot
from context_rail.runner.codegraph.probe import gitnexus_command
from context_rail.runner.codegraph.provider import (
    ExtractionError,
    command_for,
    node_version_ok,
    resolve_binary,
    version_between,
)
from context_rail.runner.run import run

__all__ = ["extract"]

MAX_SKIPPED_LISTED = 200
_LOCK_STALE = 600.0
_NODE_QUERIES = (
    ("File", "MATCH (n:File) RETURN n.id AS id, n.name AS name, n.filePath AS filePath"),
    (
        "Function",
        (
            "MATCH (n:Function) RETURN n.id AS id, n.name AS name, "
            "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
        ),
    ),
    (
        "Class",
        (
            "MATCH (n:Class) RETURN n.id AS id, n.name AS name, "
            "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
        ),
    ),
    (
        "Method",
        (
            "MATCH (n:Method) RETURN n.id AS id, n.name AS name, "
            "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
        ),
    ),
    (
        "Interface",
        (
            "MATCH (n:Interface) RETURN n.id AS id, n.name AS name, "
            "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
        ),
    ),
    (
        "Module",
        (
            "MATCH (n:Module) RETURN n.id AS id, n.name AS name, "
            "n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine"
        ),
    ),
)
_EDGE_QUERY = (
    "MATCH (a)-[r:CodeRelation]->(b) RETURN a.id AS src, r.type AS rel, "
    "b.id AS dst, r.confidence AS confidence"
)
_RELATIONS = {
    "IMPORTS": "imports",
    "CALLS": "calls",
    "EXTENDS": "inherits",
    "IMPLEMENTS": "inherits",
    "DEFINES": "contains",
    "HAS_METHOD": "contains",
    "CONTAINS": "contains",
    "ACCESSES": "references",
}
_KINDS = {
    "File": "file",
    "Function": "function",
    "Class": "class",
    "Method": "method",
    "Interface": "class",
    "Module": "module",
}


@dataclass
class _Raw:
    kind: str
    path: str
    name: str
    start: int | None
    end: int | None


def extract(
    worktree: Path,
    commit: str,
    *,
    cache_dir: Path,
    timeout_s: float,
) -> Snapshot:
    """Index ``commit`` in an isolated mirror and return the normalized snapshot."""
    worktree = Path(worktree)
    cache_dir = Path(cache_dir)
    deadline = time.monotonic() + float(timeout_s)
    binary = resolve_binary("gitnexus", os.environ.get("RAIL_GITNEXUS_BIN"))
    if binary is None:
        raise ExtractionError(
            "unavailable",
            "gitnexus is not installed",
            "npm install -g gitnexus",
        )
    command = gitnexus_command(binary)
    version = _version(command, worktree, deadline)
    node = _node_binary()
    if node is None:
        raise ExtractionError(
            "runtime_missing", "Node is not installed", "install Node 22.18+ or 24.11+"
        )
    node_version = _node_version(node, worktree, deadline)
    if not node_version_ok(node_version):
        raise ExtractionError(
            "runtime_missing",
            f"node {node_version} is outside ^22.18 || >=24.11",
            "install Node 22.18+ or 24.11+",
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    with _lock(cache_dir / "mirror.lock", deadline):
        mirror = cache_dir / "mirror"
        home = cache_dir / "home"
        _mirror(worktree, mirror, commit, deadline)
        _analyze(command, mirror, home, commit, deadline)
        rows = _queries(command, mirror, home, deadline)
        meta = _meta(mirror)
    return _normalize(rows, meta, commit, version)


def _version(command: list[str], worktree: Path, deadline: float) -> str:
    result = _run([*command, "--version"], worktree, worktree, deadline)
    if result["timed_out"]:
        raise ExtractionError("timeout", "gitnexus version check timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError("unavailable", "gitnexus version check failed: " + detail)
    version = result["stdout"].strip()
    if not version_between(version, "1.6", "1.7"):
        raise ExtractionError(
            "unsupported_version",
            f"gitnexus {version} is outside >=1.6,<1.7",
            "install gitnexus >=1.6,<1.7",
        )
    return version


def _node_version(node: str, worktree: Path, deadline: float) -> str:
    result = _run([*command_for(node), "--version"], worktree, worktree, deadline)
    if result["timed_out"]:
        raise ExtractionError("timeout", "node version check timed out")
    if result["exit"] != 0:
        raise ExtractionError(
            "runtime_missing", "node could not start", "install Node 22.18+ or 24.11+"
        )
    return result["stdout"].strip()


def _mirror(worktree: Path, mirror: Path, commit: str, deadline: float) -> None:
    if mirror.exists() and not (mirror / ".git").exists():
        shutil.rmtree(mirror, ignore_errors=True)
    _git_checked(worktree, ["worktree", "prune"], worktree, deadline, "prune")
    if not (mirror / ".git").exists():
        added = _git(
            worktree,
            ["worktree", "add", "--detach", "--force", str(mirror), commit],
            worktree,
            deadline,
        )
        _no_timeout(added, "create the gitnexus mirror")
        if added["exit"] != 0:
            detail = (added["stderr"] or added["stdout"]).strip()
            raise ExtractionError("invalid", "git could not create the gitnexus mirror: " + detail)
        return
    checked = _git(mirror, ["checkout", "--detach", "--force", commit], mirror, deadline)
    if checked["timed_out"]:
        raise ExtractionError("timeout", "checking out the gitnexus mirror timed out")
    if checked["exit"] == 0:
        return
    _git_checked(worktree, ["worktree", "remove", "--force", str(mirror)], worktree, deadline, "remove")
    added = _git(
        worktree,
        ["worktree", "add", "--detach", "--force", str(mirror), commit],
        worktree,
        deadline,
    )
    _no_timeout(added, "recreate the gitnexus mirror")
    if added["exit"] != 0:
        detail = (added["stderr"] or added["stdout"]).strip()
        raise ExtractionError("invalid", "git could not recreate the gitnexus mirror: " + detail)


def _analyze(
    command: list[str], mirror: Path, home: Path, commit: str, deadline: float
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    result = _run(
        [*command, "analyze", str(mirror), "--skip-agents-md"],
        mirror,
        mirror,
        deadline,
        {"GITNEXUS_HOME": str(home)},
    )
    if result["timed_out"]:
        raise ExtractionError("timeout", "gitnexus analyze timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError("invalid", "gitnexus analyze failed: " + (detail or "no output"))
    meta = _meta(mirror)
    if meta.get("lastCommit") != commit:
        raise ExtractionError(
            "invalid",
            f"gitnexus indexed {meta.get('lastCommit') or '(no commit)'}, not {commit}",
        )


def _queries(
    command: list[str], mirror: Path, home: Path, deadline: float
) -> dict[str, list[dict[str, str]]]:
    env = {"GITNEXUS_HOME": str(home)}
    result: dict[str, list[dict[str, str]]] = {}
    for label, query in _NODE_QUERIES:
        result[label] = _query(command, query, mirror, env, deadline)
    result["CodeRelation"] = _query(command, _EDGE_QUERY, mirror, env, deadline)
    return result


def _query(
    command: list[str],
    query: str,
    mirror: Path,
    env: dict[str, str],
    deadline: float,
) -> list[dict[str, str]]:
    result = _run(
        [*command, "cypher", query, "--repo", str(mirror)],
        mirror,
        mirror,
        deadline,
        env,
    )
    if result["timed_out"]:
        raise ExtractionError("timeout", "gitnexus cypher timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError("invalid", "gitnexus cypher failed: " + (detail or "no output"))
    return _table(result["stdout"])


def _table(text: str) -> list[dict[str, str]]:
    stripped = text.strip()
    if stripped == "[]":
        return []
    try:
        data = json.loads(stripped)
    except ValueError:
        raise ExtractionError("invalid", "gitnexus cypher printed no JSON") from None
    if not isinstance(data, dict):
        raise ExtractionError("invalid", "gitnexus cypher answered with an unknown payload")
    if data.get("error"):
        raise ExtractionError("invalid", "gitnexus cypher: " + str(data["error"]))
    markdown = data.get("markdown")
    count = data.get("row_count")
    if not isinstance(markdown, str) or not isinstance(count, int):
        raise ExtractionError("invalid", "gitnexus cypher payload has no table")
    lines = markdown.splitlines()
    if count == 0:
        return []
    if len(lines) < 2:
        raise ExtractionError("invalid", "gitnexus cypher table has no header")
    header = _cells(lines[0])
    if len(_cells(lines[1])) != len(header):
        raise ExtractionError("invalid", "gitnexus cypher table has a bad separator")
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        values = _cells(line)
        if len(values) != len(header):
            raise ExtractionError("invalid", "gitnexus cypher table has a short row")
        rows.append(dict(zip(header, values)))
    if len(rows) != count:
        raise ExtractionError("invalid", "gitnexus cypher row count does not match")
    return rows


def _cells(line: str) -> list[str]:
    text = line.strip()
    if len(text) < 2 or not text.startswith("|") or not text.endswith("|"):
        raise ExtractionError("invalid", "gitnexus cypher table row is not bounded by pipes")
    return [part.strip() for part in text[1:-1].split(" | ")]


def _normalize(
    rows: dict[str, list[dict[str, str]]],
    meta: dict[str, Any],
    commit: str,
    version: str,
) -> Snapshot:
    raw: dict[str, _Raw] = {}
    for label, entries in rows.items():
        if label == "CodeRelation":
            continue
        kind = _KINDS.get(label)
        if kind is None:
            continue
        for entry in entries:
            parsed = _raw(entry, kind)
            if parsed is not None:
                raw[entry["id"]] = parsed

    links: list[tuple[str, str, str, str]] = []
    for entry in rows.get("CodeRelation", []):
        source = entry.get("src")
        target = entry.get("dst")
        rel = _RELATIONS.get(str(entry.get("rel", "")))
        confidence = _confidence(entry.get("confidence"))
        if not source or not target or rel is None or confidence is None:
            continue
        if rel == "contains" and str(entry.get("rel")) == "HAS_METHOD" and target in raw:
            raw[target].kind = "method"
        links.append((source, target, rel, confidence))

    nodes: dict[str, Node] = {}
    resolved: dict[str, str] = {}
    for graph_id, item in raw.items():
        if item.kind == "file":
            node = Node(item.path, "file", Path(item.path).name, item.path, None, None)
        elif item.kind == "class":
            node = Node(f"{item.path}:{item.name}", "class", item.name, item.path, item.start, item.end)
        elif item.kind == "method":
            qualified = _qualified(graph_id, item.path, item.name)
            node = Node(f"{item.path}:{qualified}", "method", item.name, item.path, item.start, item.end)
        elif item.kind == "module":
            node = Node(f"{item.path}:{item.name}", "module", item.name, item.path, item.start, item.end)
        else:
            node = Node(f"{item.path}:{item.name}", "function", item.name, item.path, item.start, item.end)
        nodes[node.id] = node
        resolved[graph_id] = node.id

    edges: dict[tuple[str, str, str], Edge] = {}
    for source_gid, target_gid, rel, confidence in links:
        source = resolved.get(source_gid)
        target = resolved.get(target_gid)
        if source is None or target is None or source == target:
            continue
        edges.setdefault(
            (source, target, rel),
            Edge(source, target, rel, confidence),
        )

    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    files = _int(stats.get("files"))
    if files == 0:
        files = len({node.file for node in nodes.values()})
    return Snapshot(
        provider="gitnexus",
        provider_version=version,
        commit=commit,
        built_at=str(meta.get("indexedAt") or ""),
        calls=any(node.kind in ("function", "method") for node in nodes.values()),
        lines=any(node.start is not None for node in nodes.values()),
        files_listed=files,
        files_parsed=files,
        skipped=(),
        nodes=tuple(sorted(nodes.values(), key=lambda node: node.id)),
        edges=tuple(sorted(edges.values(), key=lambda edge: (edge.src, edge.dst, edge.rel))),
    )


def _raw(entry: dict[str, str], kind: str) -> _Raw | None:
    graph_id = str(entry.get("id") or "")
    name = str(entry.get("name") or "")
    file_path = str(entry.get("filePath") or "")
    path = _path(file_path)
    if not graph_id or not name or path is None:
        return None
    if kind == "file":
        return _Raw("file", path, name, None, None)
    start = _line(entry.get("startLine"))
    end = _line(entry.get("endLine"))
    return _Raw(kind, path, name, start, end)


def _qualified(graph_id: str, path: str, name: str) -> str:
    prefix = graph_id.split(":", 1)[1] if ":" in graph_id else ""
    qualified = prefix.split(":", 1)[1] if prefix.count(":") >= 1 else name
    if not qualified:
        return name
    # GitNexus ids are ``Kind:path:Qualified.name``; return the portion after path.
    marker = path + ":"
    if marker in graph_id:
        return graph_id.split(marker, 1)[1]
    return qualified


def _path(value: str) -> str | None:
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or ".." in text.split("/"):
        return None
    parts = text.split("/")
    if any(part in ("", ".") for part in parts):
        return None
    return text


def _line(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number + 1


def _confidence(value: Any) -> str | None:
    if value in (None, "", "1", "1.0"):
        return "extracted"
    try:
        return "extracted" if float(str(value)) >= 0.99 else "inferred"
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _meta(mirror: Path) -> dict[str, Any]:
    path = mirror / ".gitnexus" / "meta.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run(
    command: list[str],
    cwd: Path,
    confine_to: Path,
    deadline: float,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExtractionError("timeout", "gitnexus extraction passed its deadline")
    try:
        return run(
            command,
            cwd=cwd,
            confine_to=confine_to,
            timeout=remaining,
            extra_env=extra_env,
            max_output=4_000_000,
        )
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - the graph is advisory; a broken tool must not stop work
        raise ExtractionError("unavailable", f"gitnexus could not start: {exc}") from None


def _git(cwd: Path, args: list[str], confine_to: Path, deadline: float) -> dict[str, Any]:
    return _run(["git", *args], cwd, confine_to, deadline)


def _git_checked(
    cwd: Path, args: list[str], confine_to: Path, deadline: float, action: str
) -> None:
    result = _git(cwd, args, confine_to, deadline)
    _no_timeout(result, action)
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise ExtractionError("invalid", f"git could not {action}: {detail}")


def _no_timeout(result: dict[str, Any], action: str) -> None:
    if result["timed_out"]:
        raise ExtractionError("timeout", f"{action} timed out")


def _node_binary() -> str | None:
    return shutil.which("node")


@contextmanager
def _lock(path: Path, deadline: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        age = time.time() - path.stat().st_mtime
        if age > _LOCK_STALE:
            path.unlink()
    except OSError:
        pass
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, str(os.getpid()).encode("ascii"))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ExtractionError("timeout", "the gitnexus mirror is busy") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        try:
            mode = path.stat().st_mode
            if stat_module.S_ISREG(mode):
                path.unlink()
        except OSError:
            pass
