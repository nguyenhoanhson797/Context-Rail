from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.codegraph import (
    Edge,
    MemoryGraph,
    Node,
    Snapshot,
    downstream,
    parse_snapshot,
    resolve,
    snapshot_mapping,
    symbols_in,
    upstream,
)
from context_rail.model.codegraph import tests_reaching as reaching_tests
from context_rail.model.errors import ModelError


def _snapshot(
    nodes: list[Node], edges: list[Edge], *, lines: bool = True, calls: bool = True
) -> Snapshot:
    return Snapshot(
        provider="builtin",
        provider_version="1",
        commit="c1",
        built_at="2026-11-07T00:00:00Z",
        calls=calls,
        lines=lines,
        files_listed=len({node.file for node in nodes}),
        files_parsed=len({node.file for node in nodes}),
        skipped=(),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _chain() -> MemoryGraph:
    nodes = [
        Node("a.py", "file", "a.py", "a.py", None, None),
        Node("a.py:f", "function", "f", "a.py", 1, 2),
        Node("b.py:g", "function", "g", "b.py", 1, 2),
        Node("c.py:h", "function", "h", "c.py", 1, 2),
    ]
    edges = [
        Edge("a.py", "a.py:f", "contains", "extracted"),
        Edge("b.py:g", "a.py:f", "calls", "inferred"),
        Edge("c.py:h", "b.py:g", "calls", "inferred"),
    ]
    return MemoryGraph(_snapshot(nodes, edges))


def test_a_snapshot_round_trips() -> None:
    snapshot = _snapshot(
        [
            Node("a.py", "file", "a.py", "a.py", None, None),
            Node("a.py:f", "function", "f", "a.py", 1, 3),
            Node("b.py", "file", "b.py", "b.py", None, None),
        ],
        [
            Edge("a.py", "a.py:f", "contains", "extracted"),
            Edge("b.py", "a.py", "imports", "extracted"),
        ],
    )

    assert parse_snapshot(snapshot_mapping(snapshot)) == snapshot


def test_an_unknown_schema_is_refused() -> None:
    data = snapshot_mapping(_snapshot([], []))
    data["schema"] = 2

    with pytest.raises(ModelError) as caught:
        parse_snapshot(data)

    assert caught.value.code == "SCHEMA_UNSUPPORTED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["nodes"][0].update(kind="macro"),
        lambda d: d["edges"][0].update(rel="owns"),
        lambda d: d["edges"][0].update(confidence="maybe"),
        lambda d: d.update(surprise=True),
    ],
    ids=["kind", "rel", "confidence", "top-level key"],
)
def test_an_unknown_value_is_refused(mutate: Any) -> None:
    snapshot = _snapshot(
        [
            Node("a.py", "file", "a.py", "a.py", None, None),
            Node("a.py:f", "function", "f", "a.py", 1, 2),
        ],
        [Edge("a.py", "a.py:f", "contains", "extracted")],
    )
    data = snapshot_mapping(snapshot)
    mutate(data)

    with pytest.raises(ModelError) as caught:
        parse_snapshot(data)

    assert caught.value.code == "VALIDATION_FAILED"


def test_an_edge_to_an_unknown_node_is_refused() -> None:
    snapshot = _snapshot(
        [Node("a.py", "file", "a.py", "a.py", None, None)],
        [Edge("a.py", "ghost.py", "imports", "extracted")],
    )

    with pytest.raises(ModelError) as caught:
        parse_snapshot(snapshot_mapping(snapshot))

    assert caught.value.code == "VALIDATION_FAILED"


def test_upstream_walks_callers_importers_and_subclasses() -> None:
    graph = _chain()

    assert upstream(graph, ["a.py:f"], depth=2) == ("b.py:g", "c.py:h")
    assert upstream(graph, ["a.py:f"], depth=1) == ("b.py:g",)


def test_upstream_does_not_walk_containment() -> None:
    graph = _chain()

    assert upstream(graph, ["a.py:f"], depth=3) == ("b.py:g", "c.py:h")
    assert "a.py" not in upstream(graph, ["a.py:f"], depth=3)


def test_downstream_walks_what_the_nodes_depend_on() -> None:
    graph = _chain()

    assert downstream(graph, ["c.py:h"], depth=1) == ("b.py:g",)
    assert downstream(graph, ["c.py:h"], depth=2) == ("b.py:g", "a.py:f")


def test_results_are_deterministic_and_bounded() -> None:
    nodes = [Node("core.py:f", "function", "f", "core.py", 1, 2)]
    edges = []
    for index in range(80):
        caller = f"callers/c{index:02d}.py:run"
        nodes.append(Node(caller, "function", "run", f"callers/c{index:02d}.py", 1, 2))
        edges.append(Edge(caller, "core.py:f", "calls", "inferred"))
    graph = MemoryGraph(_snapshot(nodes, edges))

    found = upstream(graph, ["core.py:f"], depth=1, limit=50)

    assert len(found) == 50
    assert found == tuple(sorted(found))
    assert found == upstream(graph, ["core.py:f"], depth=1, limit=50)


def test_symbols_in_maps_line_ranges_to_the_innermost_symbol() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("a.py", "file", "a.py", "a.py", None, None),
                Node("a.py:C", "class", "C", "a.py", 1, 9),
                Node("a.py:C.m", "method", "m", "a.py", 3, 5),
            ],
            [],
        )
    )

    assert symbols_in(graph, "a.py", [(4, 4)]) == ("a.py:C.m",)
    assert symbols_in(graph, "a.py", [(8, 8)]) == ("a.py:C",)
    assert symbols_in(graph, "a.py", [(4, 4), (8, 8)]) == ("a.py:C", "a.py:C.m")


def test_symbols_in_a_file_without_line_data_maps_to_the_file_node() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("web/x.ts", "file", "x.ts", "web/x.ts", None, None),
            ],
            [],
            lines=False,
        )
    )

    assert symbols_in(graph, "web/x.ts", [(10, 12)]) == ("web/x.ts",)


def test_symbols_in_a_line_outside_every_span_maps_to_the_file_node() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("a.py", "file", "a.py", "a.py", None, None),
                Node("a.py:f", "function", "f", "a.py", 3, 4),
            ],
            [],
        )
    )

    assert symbols_in(graph, "a.py", [(1, 1)]) == ("a.py",)


def test_symbols_in_an_unknown_file_is_empty() -> None:
    assert symbols_in(_chain(), "nope.py", [(1, 1)]) == ()


def test_resolve_handles_paths_and_symbols() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("a.py", "file", "a.py", "a.py", None, None),
                Node("a.py:C", "class", "C", "a.py", 1, 9),
                Node("a.py:C.m", "method", "m", "a.py", 3, 5),
                Node("a.py:other", "function", "other", "a.py", 10, 11),
                Node("b.py:other", "function", "other", "b.py", 1, 2),
            ],
            [],
        )
    )

    assert resolve(graph, "a.py") == ("a.py",)
    assert resolve(graph, "a.py:C.m") == ("a.py:C.m",)
    assert resolve(graph, "a.py:m") == ("a.py:C.m",)
    assert resolve(graph, "nope.py") == ()
    assert resolve(graph, "a.py:nope") == ()


def test_resolve_returns_every_candidate_when_a_symbol_is_ambiguous() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("a.py", "file", "a.py", "a.py", None, None),
                Node("a.py:A.run", "method", "run", "a.py", 2, 3),
                Node("a.py:B.run", "method", "run", "a.py", 5, 6),
            ],
            [],
        )
    )

    assert resolve(graph, "a.py:run") == ("a.py:A.run", "a.py:B.run")


def test_tests_reaching_keeps_only_test_files() -> None:
    graph = MemoryGraph(
        _snapshot(
            [
                Node("a.py:f", "function", "f", "a.py", 1, 2),
                Node("b.py:g", "function", "g", "b.py", 1, 2),
                Node("tests/test_a.py:test_f", "function", "test_f", "tests/test_a.py", 1, 2),
            ],
            [
                Edge("b.py:g", "a.py:f", "calls", "inferred"),
                Edge("tests/test_a.py:test_f", "b.py:g", "calls", "inferred"),
            ],
        )
    )

    assert reaching_tests(graph, ["a.py:f"]) == ("tests/test_a.py:test_f",)
