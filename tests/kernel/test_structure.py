"""Structure rules: executable, blocked, stuck and score rollup."""

from __future__ import annotations

import pytest

from context_rail.kernel.structure import ItemRow, Score, blocked, executable, rollup, stuck


def row(
    slug, category="not_started", part_of=(), depends_on=(), last=None, container=None
):
    return ItemRow(
        slug, category, tuple(part_of), tuple(depends_on), last, container=container
    )


def test_executable_excludes_parents_blocked_and_done():
    items = [ItemRow("g", "active", (), (), None), ItemRow("t1", "not_started", ("g",), (), None),
             ItemRow("t2", "not_started", ("g",), ("t1",), None), ItemRow("t3", "done", ("g",), (), None)]
    assert executable(items) == ["t1"]
    assert blocked(items) == {"t2": "depends_on:t1"}


def test_executable_includes_active_leaves_and_is_sorted():
    items = [row("m", "not_started"), row("b", "active"), row("a", "active"),
             row("c", "dropped"), row("d", "blocked"), row("e", "done")]
    assert executable(items) == ["a", "b", "m"]


def test_executable_treats_a_missing_dependency_as_unfinished():
    assert executable([row("a", depends_on=("ghost",))]) == []
    assert blocked([row("a", depends_on=("ghost",))]) == {"a": "depends_on:ghost"}


def test_executable_treats_a_dropped_dependency_as_finished():
    items = [row("a", depends_on=("d",)), row("d", "dropped"), row("b", depends_on=("x",)),
             row("x", "done")]
    assert executable(items) == ["a", "b"]
    assert blocked(items) == {}


def test_blocked_status_takes_precedence_and_reports_first_unfinished_dependency():
    items = [row("y", "not_started"), row("x", "blocked", depends_on=("y",)),
             row("a", "not_started"), row("b", "not_started"),
             row("z", "not_started", depends_on=("b", "a")),
             row("w", "not_started", depends_on=("m",)),
             row("d", "dropped"), row("v", "not_started", depends_on=("d",))]
    assert blocked(items) == {"x": "status", "z": "depends_on:a", "w": "depends_on:m"}


def test_blocked_reports_finished_items_for_the_caller_to_drop():
    items = [row("open"), row("done", "done", depends_on=("open",)),
             row("dropped", "dropped", depends_on=("open",)),
             row("halted", "blocked")]
    assert blocked(items) == {"done": "depends_on:open", "dropped": "depends_on:open",
                              "halted": "status"}


def test_stuck():
    items = [ItemRow("a", "active", (), (), 0), ItemRow("b", "active", (), (), 9_000), ItemRow("c", "not_started", (), (), None)]
    assert stuck(items, now_ms=10_000, stuck_ms=5_000) == ["a"]


def test_stuck_covers_active_items_without_activity_and_is_sorted():
    items = [row("z", "active", last=None), row("y", "active", last=4_999), row("x", "active", last=5_000),
             row("w", "not_started", last=None), row("v", "blocked", last=None)]
    assert stuck(items, now_ms=10_000, stuck_ms=5_000) == ["y", "z"]


def test_rollup_sums_descendants():
    items = [ItemRow("g", "active", (), (), None), ItemRow("t", "active", ("g",), (), None)]
    s = rollup(items, {"c1": "t", "c2": "t", "c3": "g"}, {"c1": True, "c2": None, "c3": True})
    assert s["t"] == Score(1, 2) and s["g"] == Score(2, 3)


def test_rollup_counts_a_diamond_descendant_once_per_ancestor():
    items = [row("g"), row("a", part_of=("g",)), row("b", part_of=("g",)),
             row("x", part_of=("a", "b"))]
    s = rollup(items, {"c1": "x"}, {"c1": True})
    assert s["x"] == s["a"] == s["b"] == s["g"] == Score(1, 1)


def test_rollup_counts_a_multi_parent_descendant_once_per_ancestor():
    items = [row("g"), row("a", part_of=("g",)), row("x", part_of=("a", "g"))]
    s = rollup(items, {"c1": "x"}, {"c1": True})
    assert s["g"] == Score(1, 1)


def test_rollup_missing_verdicts_count_as_unproven_and_unknown_items_ignored():
    items = [row("t")]
    s = rollup(items, {"c1": "t", "c2": "t", "c9": "ghost"}, {"c1": True})
    assert s == {"t": Score(1, 2)}


def test_rollup_cycle_refused():
    with pytest.raises(ValueError) as loop:
        rollup([row("x", part_of=("x",))], {}, {})
    assert str(loop.value) == "cycle"
    with pytest.raises(ValueError) as pair:
        rollup([row("a", part_of=("b",)), row("b", part_of=("a",))], {}, {})
    assert str(pair.value) == "cycle"


def test_rollup_cycle_behind_a_leaf_is_refused():
    items = [row("g"), row("t", part_of=("g",)), row("a", part_of=("b",)), row("b", part_of=("a",))]
    with pytest.raises(ValueError) as cycle:
        rollup(items, {}, {})
    assert str(cycle.value) == "cycle"


def test_part_of_a_missing_item_does_not_raise():
    assert rollup([row("a", part_of=("ghost",))], {}, {}) == {"a": Score(0, 0)}


def test_container_true_excludes_even_without_children():
    assert executable([row("sprint", container=True)]) == []
    assert executable([row("task", container=False)]) == ["task"]


def test_container_false_with_a_child_stays_executable():
    items = [row("task", container=False), row("check", part_of=("task",))]
    assert executable(items) == ["check", "task"]


def test_container_none_with_a_child_keeps_the_legacy_exclusion():
    items = [row("goal"), row("task", part_of=("goal",))]
    assert executable(items) == ["task"]


def test_container_true_with_children_is_not_executable():
    items = [row("sprint", container=True), row("task", part_of=("sprint",))]
    assert executable(items) == ["task"]


def test_category_and_role_vocabulary_comes_from_the_model() -> None:
    from context_rail.gateway.handlers.dispatch import common as dispatch_common
    from context_rail.kernel import structure
    from context_rail.model import primitives
    from context_rail.store import adapters

    assert structure.FINISHED_CATEGORIES is primitives.FINISHED_CATEGORIES
    assert structure.WORKABLE_CATEGORIES is primitives.WORKABLE_CATEGORIES
    assert dispatch_common.IMPL_ROLE == primitives.ROLES[1]
    assert dispatch_common.REVIEW_ROLE == primitives.ROLES[2]
    assert adapters.CLAUDE_ROLES == primitives.ROLES


def test_the_windows_reserved_set_has_one_definition() -> None:
    from context_rail.model import primitives
    from context_rail.store import adapters, location

    assert location.WINDOWS_RESERVED_NAMES is primitives.WINDOWS_RESERVED_NAMES
    assert adapters.WINDOWS_RESERVED_NAMES is primitives.WINDOWS_RESERVED_NAMES


def test_a_dependency_cycle_is_reported_not_merely_blocked() -> None:
    from context_rail.kernel.structure import ItemRow, blocked, cycles, executable

    items = [
        ItemRow("a", "not_started", (), ("b",), None),
        ItemRow("b", "not_started", (), ("c",), None),
        ItemRow("c", "not_started", (), ("a",), None),
    ]

    assert cycles(items) == frozenset({"a", "b", "c"})
    assert blocked(items) == {"a": "cycle", "b": "cycle", "c": "cycle"}
    assert executable(items) == []
