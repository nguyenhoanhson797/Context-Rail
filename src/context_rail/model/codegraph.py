"""The code graph snapshot and the questions asked of it.

One normalized picture of a commit's code, whatever produced it. This module is
pure: it reads no file, runs no process and touches no index. A snapshot that
uses a schema, kind, relation or confidence it does not know is refused whole.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, NoReturn, Protocol

from context_rail.model.errors import ModelError

__all__ = [
    "CONFIDENCES",
    "CONFIG_CHOICES",
    "EDGE_RELS",
    "NODE_KINDS",
    "PACK_CODE_GRAPH_CHOICES",
    "SNAPSHOT_SCHEMA",
    "TEST_GLOBS",
    "Edge",
    "MemoryGraph",
    "Neighbors",
    "Node",
    "Snapshot",
    "code_graph_source",
    "downstream",
    "effective_code_graph",
    "parse_snapshot",
    "resolve",
    "snapshot_mapping",
    "symbols_in",
    "tests_reaching",
    "upstream",
]

CONFIG_CHOICES = ("builtin", "graphify", "gitnexus", "none")
#: A pack only says whether a graph is relevant; choosing a tool is the team's.
PACK_CODE_GRAPH_CHOICES = ("builtin", "none")
SNAPSHOT_SCHEMA = 1
PROVIDERS = ("builtin", "graphify", "gitnexus")
NODE_KINDS = ("file", "module", "class", "function", "method")
EDGE_RELS = ("contains", "imports", "calls", "inherits", "references")
CONFIDENCES = ("extracted", "inferred")
TEST_GLOBS = ("tests/**", "**/test_*", "**/*_test.*", "**/*.test.*", "**/*.spec.*")

#: Relations that mean "depends on". Containment is structure, not dependence.
_DEPENDENCY_RELS = frozenset({"imports", "calls", "inherits", "references"})

_SNAPSHOT_KEYS = (
    "schema",
    "provider",
    "provider_version",
    "commit",
    "built_at",
    "capabilities",
    "coverage",
    "nodes",
    "edges",
)
_CAPABILITY_KEYS = ("calls", "lines")
_COVERAGE_KEYS = ("files_listed", "files_parsed", "skipped")
_NODE_KEYS = ("id", "kind", "name", "file", "start", "end")
_EDGE_KEYS = ("src", "dst", "rel", "confidence")


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    name: str
    file: str
    start: int | None
    end: int | None


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str
    confidence: str


@dataclass(frozen=True)
class Snapshot:
    provider: str
    provider_version: str
    commit: str
    built_at: str
    calls: bool
    lines: bool
    files_listed: int
    files_parsed: int
    skipped: tuple[str, ...]
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]


class Neighbors(Protocol):
    """What the graph queries need from a store of nodes and edges."""

    def node(self, node_id: str) -> Node | None: ...

    def by_file(self, path: str) -> tuple[Node, ...]: ...

    def by_name(self, name: str) -> tuple[Node, ...]: ...

    def incoming(self, node_id: str) -> tuple[Edge, ...]: ...

    def outgoing(self, node_id: str) -> tuple[Edge, ...]: ...


class MemoryGraph:
    """A snapshot held in memory, indexed for the neighbor protocol."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self._nodes = {node.id: node for node in snapshot.nodes}
        self._files: dict[str, list[Node]] = {}
        self._names: dict[str, list[Node]] = {}
        for node in snapshot.nodes:
            self._files.setdefault(node.file, []).append(node)
            self._names.setdefault(node.name, []).append(node)
        self._in: dict[str, list[Edge]] = {}
        self._out: dict[str, list[Edge]] = {}
        for edge in snapshot.edges:
            self._in.setdefault(edge.dst, []).append(edge)
            self._out.setdefault(edge.src, []).append(edge)

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def by_file(self, path: str) -> tuple[Node, ...]:
        return tuple(self._files.get(path, ()))

    def by_name(self, name: str) -> tuple[Node, ...]:
        return tuple(self._names.get(name, ()))

    def incoming(self, node_id: str) -> tuple[Edge, ...]:
        return tuple(self._in.get(node_id, ()))

    def outgoing(self, node_id: str) -> tuple[Edge, ...]:
        return tuple(self._out.get(node_id, ()))


def effective_code_graph(configured: str | None, pack_default: str | None) -> str:
    """The provider choice in force: ``rail.yaml``, else the pack, else ``builtin``."""
    if configured is not None:
        return configured
    if pack_default is not None:
        return pack_default
    return "builtin"


def code_graph_source(configured: str | None, pack_default: str | None) -> str:
    """Where ``effective_code_graph`` got its value: ``rail.yaml``, ``pack`` or ``default``."""
    if configured is not None:
        return "rail.yaml"
    if pack_default is not None:
        return "pack"
    return "default"


def snapshot_mapping(snapshot: Snapshot) -> dict[str, Any]:
    """The plain-data form of a snapshot (``parse_snapshot`` reads it back)."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "provider": snapshot.provider,
        "provider_version": snapshot.provider_version,
        "commit": snapshot.commit,
        "built_at": snapshot.built_at,
        "capabilities": {"calls": snapshot.calls, "lines": snapshot.lines},
        "coverage": {
            "files_listed": snapshot.files_listed,
            "files_parsed": snapshot.files_parsed,
            "skipped": list(snapshot.skipped),
        },
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "file": node.file,
                "start": node.start,
                "end": node.end,
            }
            for node in snapshot.nodes
        ],
        "edges": [
            {"src": e.src, "dst": e.dst, "rel": e.rel, "confidence": e.confidence}
            for e in snapshot.edges
        ],
    }


def parse_snapshot(data: Mapping[str, Any]) -> Snapshot:
    """Read a snapshot mapping, refusing anything outside schema 1."""
    if not isinstance(data, Mapping):
        _refuse("a code graph snapshot must be a mapping")
    if data.get("schema") != SNAPSHOT_SCHEMA:
        raise ModelError(
            "SCHEMA_UNSUPPORTED",
            f"unsupported code graph snapshot schema: {data.get('schema')!r}",
            {"field": "schema"},
        )
    _closed(data, _SNAPSHOT_KEYS, "snapshot")
    missing = [key for key in _SNAPSHOT_KEYS if key not in data]
    if missing:
        _refuse("snapshot is missing " + ", ".join(missing), "snapshot")

    provider = _text(data["provider"], "provider")
    if provider not in PROVIDERS:
        _refuse(f"unknown provider {provider!r}", "provider")
    capabilities = _mapping(data["capabilities"], "capabilities")
    _closed(capabilities, _CAPABILITY_KEYS, "capabilities")
    coverage = _mapping(data["coverage"], "coverage")
    _closed(coverage, _COVERAGE_KEYS, "coverage")

    nodes = tuple(_node(raw) for raw in _list(data["nodes"], "nodes"))
    ids = [node.id for node in nodes]
    if len(set(ids)) != len(ids):
        _refuse("duplicate node id", "nodes")
    known = set(ids)
    edges = tuple(_edge(raw, known) for raw in _list(data["edges"], "edges"))
    skipped = tuple(_text(item, "skipped") for item in _list(coverage["skipped"], "skipped"))

    return Snapshot(
        provider=provider,
        provider_version=_text(data["provider_version"], "provider_version"),
        commit=_text(data["commit"], "commit"),
        built_at=_text(data["built_at"], "built_at"),
        calls=_flag(capabilities["calls"], "capabilities.calls"),
        lines=_flag(capabilities["lines"], "capabilities.lines"),
        files_listed=_count(coverage["files_listed"], "files_listed"),
        files_parsed=_count(coverage["files_parsed"], "files_parsed"),
        skipped=skipped,
        nodes=nodes,
        edges=edges,
    )


def resolve(graph: Neighbors, target: str) -> tuple[str, ...]:
    """Node ids a ``path`` or ``path:Symbol`` target names.

    More than one id means the target is ambiguous; none means it is unknown.
    """
    path, separator, symbol = target.partition(":")
    if not separator:
        found = graph.node(path)
        return (found.id,) if found is not None and found.kind == "file" else ()
    exact = graph.node(target)
    if exact is not None:
        return (exact.id,)
    prefix = path + ":"
    matches = []
    for node in graph.by_file(path):
        if node.kind == "file" or not node.id.startswith(prefix):
            continue
        qualified = node.id[len(prefix) :]
        if qualified == symbol or qualified.endswith("." + symbol):
            matches.append(node.id)
    return tuple(sorted(matches))


def upstream(
    graph: Neighbors, ids: Iterable[str], depth: int = 2, limit: int = 50
) -> tuple[str, ...]:
    """Callers, importers and subclasses of ``ids``, nearest first."""
    return _walk(graph, ids, depth, limit, backwards=True)


def downstream(
    graph: Neighbors, ids: Iterable[str], depth: int = 1, limit: int = 50
) -> tuple[str, ...]:
    """What ``ids`` depend on, nearest first."""
    return _walk(graph, ids, depth, limit, backwards=False)


def symbols_in(
    graph: Neighbors, path: str, ranges: Sequence[tuple[int, int]]
) -> tuple[str, ...]:
    """The innermost symbols whose spans cover the changed line ranges.

    Lines no symbol covers, and files without line data, map to the file node.
    """
    nodes = graph.by_file(path)
    file_node = next((node for node in nodes if node.kind == "file"), None)
    if file_node is None:
        return ()
    spans = [
        (node, node.start, node.end)
        for node in nodes
        if node.kind != "file" and node.start is not None and node.end is not None
    ]
    found: set[str] = set()
    for start, end in ranges:
        lo, hi = min(start, end), max(start, end)
        clipped = [
            (node, max(lo, first), min(hi, last))
            for node, first, last in spans
            if first <= hi and last >= lo
        ]
        uncovered = [(lo, hi)]
        for node, first, last in clipped:
            nested = [
                (inner_first, inner_last)
                for inner, inner_first, inner_last in clipped
                if inner.id != node.id and _inside(inner, node)
            ]
            if _remaining((first, last), nested):
                found.add(node.id)
            uncovered = _subtract(uncovered, (first, last))
        if uncovered:
            found.add(file_node.id)
    return tuple(sorted(found))


def tests_reaching(
    graph: Neighbors, ids: Iterable[str], depth: int = 3, limit: int = 20
) -> tuple[str, ...]:
    """Upstream nodes that live in test files."""
    reached = upstream(graph, ids, depth=depth, limit=10_000)
    tests = []
    for node_id in reached:
        node = graph.node(node_id)
        if node is not None and _is_test_file(node.file):
            tests.append(node_id)
    return tuple(tests[:limit])


def _walk(
    graph: Neighbors, ids: Iterable[str], depth: int, limit: int, *, backwards: bool
) -> tuple[str, ...]:
    if depth < 1 or limit < 1:
        return ()
    seeds = set(ids)
    seen = set(seeds)
    frontier = sorted(seeds)
    found: list[str] = []
    for _ in range(depth):
        level: set[str] = set()
        for node_id in frontier:
            edges = graph.incoming(node_id) if backwards else graph.outgoing(node_id)
            for edge in edges:
                if edge.rel not in _DEPENDENCY_RELS:
                    continue
                other = edge.src if backwards else edge.dst
                if other not in seen:
                    level.add(other)
        frontier = sorted(level)
        seen.update(frontier)
        found.extend(frontier)
        if len(found) >= limit:
            return tuple(found[:limit])
        if not frontier:
            break
    return tuple(found)


def _inside(inner: Node, outer: Node) -> bool:
    if inner.start is None or inner.end is None or outer.start is None or outer.end is None:
        return False
    if not (inner.start >= outer.start and inner.end <= outer.end):
        return False
    smaller = (inner.end - inner.start) < (outer.end - outer.start)
    return smaller or inner.id.startswith(outer.id + ".")


def _remaining(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    left = [span]
    for piece in taken:
        left = _subtract(left, piece)
    return bool(left)


def _subtract(
    spans: list[tuple[int, int]], piece: tuple[int, int]
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in spans:
        if piece[1] < lo or piece[0] > hi:
            out.append((lo, hi))
            continue
        if piece[0] > lo:
            out.append((lo, piece[0] - 1))
        if piece[1] < hi:
            out.append((piece[1] + 1, hi))
    return out


def _is_test_file(path: str) -> bool:
    for pattern in TEST_GLOBS:
        if fnmatchcase(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
            return True
    return False


def _node(raw: Any) -> Node:
    item = _mapping(raw, "node")
    _closed(item, _NODE_KEYS, "node")
    missing = [key for key in _NODE_KEYS if key not in item]
    if missing:
        _refuse("node is missing " + ", ".join(missing), "nodes")
    kind = _text(item["kind"], "kind")
    if kind not in NODE_KINDS:
        _refuse(f"unknown node kind {kind!r}", "kind")
    start, end = item["start"], item["end"]
    if (start is None) != (end is None):
        _refuse("a node span needs both start and end", "nodes")
    if start is not None and (not _is_int(start) or not _is_int(end) or start < 1 or end < start):
        _refuse("a node span must be 1-based with start <= end", "nodes")
    return Node(
        id=_text(item["id"], "id"),
        kind=kind,
        name=_text(item["name"], "name"),
        file=_text(item["file"], "file"),
        start=start,
        end=end,
    )


def _edge(raw: Any, known: set[str]) -> Edge:
    item = _mapping(raw, "edge")
    _closed(item, _EDGE_KEYS, "edge")
    missing = [key for key in _EDGE_KEYS if key not in item]
    if missing:
        _refuse("edge is missing " + ", ".join(missing), "edges")
    rel = _text(item["rel"], "rel")
    if rel not in EDGE_RELS:
        _refuse(f"unknown edge relation {rel!r}", "rel")
    confidence = _text(item["confidence"], "confidence")
    if confidence not in CONFIDENCES:
        _refuse(f"unknown edge confidence {confidence!r}", "confidence")
    src, dst = _text(item["src"], "src"), _text(item["dst"], "dst")
    for end in (src, dst):
        if end not in known:
            _refuse(f"edge points at unknown node {end!r}", "edges")
    return Edge(src=src, dst=dst, rel=rel, confidence=confidence)


def _closed(data: Mapping[str, Any], keys: Sequence[str], label: str) -> None:
    unknown = sorted(set(data) - set(keys))
    if unknown:
        _refuse(f"unknown {label} key(s): " + ", ".join(map(str, unknown)), label)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _refuse(f"{field} must be a mapping", field)
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        _refuse(f"{field} must be a list", field)
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _refuse(f"{field} must be a non-empty string", field)
    return value


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _refuse(f"{field} must be true or false", field)
    return value


def _count(value: Any, field: str) -> int:
    if not _is_int(value) or value < 0:
        _refuse(f"{field} must be a non-negative integer", field)
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _refuse(message: str, field: str = "snapshot") -> NoReturn:
    raise ModelError("VALIDATION_FAILED", message, {"field": field})
