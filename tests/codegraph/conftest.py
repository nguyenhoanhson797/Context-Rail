from __future__ import annotations

from collections.abc import Callable

import pytest

from context_rail.model.codegraph import Edge, Node, Snapshot

SnapshotFactory = Callable[..., Snapshot]


@pytest.fixture
def make_snapshot() -> SnapshotFactory:
    """A small snapshot for ``commit``: file a.py, ``extra`` callers of a.py:f."""

    def build(commit: str = "c1", *, extra: int = 0, provider: str = "builtin") -> Snapshot:
        nodes = [
            Node("a.py", "file", "a.py", "a.py", None, None),
            Node("a.py:f", "function", "f", "a.py", 1, 3),
        ]
        edges = [Edge("a.py", "a.py:f", "contains", "extracted")]
        for index in range(extra):
            path = f"callers/c{index:03d}.py"
            nodes.append(Node(path, "file", path, path, None, None))
            nodes.append(Node(f"{path}:run", "function", "run", path, 1, 2))
            edges.append(Edge(f"{path}:run", "a.py:f", "calls", "inferred"))
        return Snapshot(
            provider=provider,
            provider_version="1",
            commit=commit,
            built_at="2026-11-07T00:00:00Z",
            calls=True,
            lines=True,
            files_listed=len(nodes),
            files_parsed=len(nodes),
            skipped=(),
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

    return build
