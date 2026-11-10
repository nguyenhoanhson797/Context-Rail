"""Reach closure: pure graph walks over plain mappings."""

from __future__ import annotations

from context_rail.kernel.reach import ancestors, descendants, one_hop, under_prefix

CHILDREN = {
    "goal": ["feature-a", "feature-b"],
    "feature-a": ["task-1", "task-2"],
    "feature-b": ["task-3"],
}
PARENTS = {
    "task-1": ["feature-a"],
    "task-2": ["feature-a"],
    "task-3": ["feature-b"],
    "feature-a": ["goal"],
    "feature-b": ["goal"],
    "goal": ["vision"],
}


def test_descendants_follow_children_and_include_root() -> None:
    assert descendants("feature-a", CHILDREN) == {"feature-a", "task-1", "task-2"}
    assert descendants("goal", CHILDREN) == {
        "goal",
        "feature-a",
        "feature-b",
        "task-1",
        "task-2",
        "task-3",
    }
    assert descendants("task-1", CHILDREN) == {"task-1"}
    assert descendants("ghost", CHILDREN) == {"ghost"}


def test_descendants_survive_a_cycle() -> None:
    loop = {"a": ["b"], "b": ["c"], "c": ["a"]}

    assert descendants("a", loop) == {"a", "b", "c"}


def test_a_document_with_two_parents_is_reached_through_either() -> None:
    children = {"feature-a": ["shared"], "feature-b": ["shared", "own"]}

    assert "shared" in descendants("feature-a", children)
    assert "shared" in descendants("feature-b", children)
    assert "own" not in descendants("feature-a", children)


def test_ancestors_respect_the_level_bound() -> None:
    assert ancestors("task-1", PARENTS, 1) == {"feature-a"}
    assert ancestors("task-1", PARENTS, 2) == {"feature-a", "goal"}
    assert ancestors("task-1", PARENTS, None) == {"feature-a", "goal", "vision"}
    assert ancestors("task-1", PARENTS, 0) == frozenset()
    assert ancestors("vision", PARENTS, None) == frozenset()


def test_ancestors_survive_a_cycle_and_exclude_the_start() -> None:
    loop = {"a": ["b"], "b": ["a"]}

    assert ancestors("a", loop, None) == {"b"}


def test_one_hop_is_outgoing_only() -> None:
    edges = {"task-1": ["task-9"], "task-8": ["task-1"]}

    assert one_hop(["task-1"], edges) == {"task-9"}
    assert one_hop(["task-9"], edges) == frozenset()
    assert one_hop(["task-1", "task-8"], edges) == {"task-9", "task-1"}


def test_under_prefix_normalises_backslashes_and_refuses_dotdot() -> None:
    assert under_prefix("src/orders", "src/orders/a.py")
    assert under_prefix("src/orders", "src\\orders\\a.py")
    assert not under_prefix("src/orders", "src/orders2/a.py")
    assert not under_prefix("src/orders", "src/../x")
    assert not under_prefix("src/orders", "C:/src/orders/a.py")
    assert under_prefix("", "anything.py")
