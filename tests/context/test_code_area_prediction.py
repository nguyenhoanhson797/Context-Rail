"""The code area is predicted and rendered from a graph alone."""

from __future__ import annotations

from context_rail.context import code_area
from context_rail.model.codegraph import Edge, MemoryGraph, Node, Snapshot


def _graph(nodes: tuple[Node, ...], edges: tuple[Edge, ...] = ()) -> MemoryGraph:
    return MemoryGraph(
        Snapshot(
            provider="builtin",
            provider_version="1",
            commit="c1",
            built_at="2026-11-07T00:00:00Z",
            calls=True,
            lines=True,
            files_listed=1,
            files_parsed=1,
            skipped=(),
            nodes=nodes,
            edges=edges,
        )
    )


def test_no_seeds_means_no_predicted_area() -> None:
    graph = _graph((Node("a.py", "file", "a.py", "a.py", None, None),))

    area = code_area.predict_area(graph, text="Make it faster.")

    assert area.seeds == ()
    assert code_area.render_area(graph_line="graph: builtin @c1", area=area) == (
        "no code area predicted (mention a path or path:Symbol in the item)"
    )


def test_an_ambiguous_mention_is_not_a_seed() -> None:
    graph = _graph(
        (
            Node("a.py", "file", "a.py", "a.py", None, None),
            Node("a.py:A.run", "method", "run", "a.py", 1, 2),
            Node("a.py:B.run", "method", "run", "a.py", 3, 4),
        ),
        (Edge("a.py", "a.py:A.run", "contains", "extracted"),),
    )

    assert code_area.predict_area(graph, text="see a.py:run").seeds == ()
    assert code_area.predict_area(graph, text="see a.py:A.run").seeds == ("a.py:A.run",)


def test_the_rendered_area_never_exceeds_its_bound() -> None:
    area = code_area.Area(
        seeds=tuple(
            f"src/billing/discounts/module_{index:03d}.py:apply_function_{index:03d}"
            for index in range(50)
        ),
        callers=(),
    )

    text = code_area.render_area(graph_line="graph: builtin @abc1234 (fresh)", area=area)

    assert len(text) <= code_area.CODE_AREA_CHARS
    assert text.startswith("graph: builtin @abc1234 (fresh)")
    assert "more)" in text
