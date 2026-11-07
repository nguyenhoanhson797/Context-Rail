"""A code target answers from the cache fast, on a large graph and a large store."""

from __future__ import annotations

import pytest
from helpers import median_ms
from perf.gen import make_store

from context_rail.context import surface
from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers import views
from context_rail.model.codegraph import Edge, Node, Snapshot
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.codegraph import save_snapshot

FILES = 500
PER_FILE = 9


def _large_snapshot() -> Snapshot:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for number in range(FILES):
        path = f"src/m{number}.py"
        nodes.append(Node(path, "file", f"m{number}.py", path, None, None))
        for index in range(PER_FILE):
            symbol = f"{path}:f{index}"
            nodes.append(Node(symbol, "function", f"f{index}", path, index * 4 + 1, index * 4 + 3))
            edges.append(Edge(path, symbol, "contains", "extracted"))
            edges.append(Edge(symbol, f"src/m{(number + 1) % FILES}.py:f{index}", "calls", "inferred"))
            edges.append(Edge(symbol, f"src/m{(number + 7) % FILES}.py:f{(index + 1) % PER_FILE}", "calls", "inferred"))
    return Snapshot(
        provider="builtin",
        provider_version="1",
        commit="a" * 40,
        built_at="2026-11-07T00:00:00Z",
        calls=True,
        lines=True,
        files_listed=FILES,
        files_parsed=FILES,
        skipped=(),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


@pytest.mark.perf
def test_code_targets_are_fast_on_a_large_graph(tmp_path) -> None:
    store = make_store(tmp_path, 1000)
    snapshot = _large_snapshot()
    assert len(snapshot.nodes) >= 5000 and len(snapshot.edges) >= 13000
    save_snapshot(store, snapshot)
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("basic", store),
        actor="cto",
    )
    target = "src/m42.py:f3"

    first = surface.why(context, target)
    assert first["callers"], first
    assert median_ms(lambda: surface.why(context, target), 5) < 80
    assert median_ms(lambda: views.show(context, {"slug": target, "body_chars": 0}, "cto"), 5) < 80
    assert median_ms(lambda: surface.why(context, "src/m42.py"), 5) < 80
