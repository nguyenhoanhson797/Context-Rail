"""Typed relation endpoints and reachability."""

from __future__ import annotations

import pytest

from context_rail.kernel.relations import endpoint_problem, reachable


@pytest.mark.parametrize(
    ("relation", "source", "target"),
    [
        ("part_of", "item", "item"),
        ("depends_on", "item", "item"),
        ("verifies", "check", "item"),
        ("owned_by", "item", "actor"),
    ],
)
def test_valid_typed_edges(relation: str, source: str, target: str) -> None:
    assert endpoint_problem(relation, source, target) is None


@pytest.mark.parametrize(
    ("relation", "source", "target"),
    [
        ("part_of", "note", "item"),
        ("part_of", "item", "note"),
        ("depends_on", "item", "check"),
        ("verifies", "item", "item"),
        ("verifies", "check", "note"),
        ("owned_by", "item", "item"),
        ("owned_by", "note", "actor"),
    ],
)
def test_wrong_endpoints_are_refused(relation: str, source: str, target: str) -> None:
    assert endpoint_problem(relation, source, target)


@pytest.mark.parametrize("relation", ["about", "because_of", "supersedes", "resolves"])
def test_permissive_relations_accept_any_pair(relation: str) -> None:
    assert endpoint_problem(relation, "note", "actor") is None


def test_a_self_edge_is_refused_for_a_typed_relation() -> None:
    assert endpoint_problem(
        "part_of", "item", "item", source_slug="a", target_slug="a"
    )


def test_reachable_walks_only_the_given_edges() -> None:
    edges = {"a": ["b"], "b": ["c"], "c": []}

    assert reachable(edges, "a", "c")
    assert not reachable(edges, "c", "a")
    assert reachable({"a": ["a"]}, "a", "a")
