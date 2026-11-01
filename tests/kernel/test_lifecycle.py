"""Lifecycle rules: transitions and category mapping come from the pack."""

from __future__ import annotations

import pytest

from context_rail.kernel.lifecycle import category_of, check_transition


def test_basic_pack_transitions(basic_pack):
    assert check_transition(basic_pack, "todo", "doing") is None
    assert check_transition(basic_pack, "todo", "done") is not None
    assert check_transition(basic_pack, "done", "nope") is not None


@pytest.mark.parametrize(
    ("current", "target"),
    [("todo", "doing"), ("todo", "dropped"), ("doing", "blocked"), ("doing", "done"),
     ("doing", "todo"), ("blocked", "doing"), ("done", "doing"), ("dropped", "todo")],
)
def test_declared_transitions_are_allowed(basic_pack, current, target):
    assert check_transition(basic_pack, current, target) is None


@pytest.mark.parametrize(
    ("current", "target"),
    [("todo", "blocked"), ("doing", "doing"), ("blocked", "done"), ("done", "todo"),
     ("dropped", "done")],
)
def test_undeclared_transitions_are_refused_with_a_reason(basic_pack, current, target):
    reason = check_transition(basic_pack, current, target)
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize(
    ("current", "target"),
    [("nope", "doing"), ("todo", "nope"), ("nope", "nope"), ("", "todo")],
)
def test_unknown_statuses_are_refused(basic_pack, current, target):
    reason = check_transition(basic_pack, current, target)
    assert isinstance(reason, str) and reason


def test_category_of_maps_every_small_status(basic_pack):
    assert category_of(basic_pack, "todo") == "not_started"
    assert category_of(basic_pack, "doing") == "active"
    assert category_of(basic_pack, "blocked") == "blocked"
    assert category_of(basic_pack, "done") == "done"
    assert category_of(basic_pack, "dropped") == "dropped"
    assert category_of(basic_pack, "nope") is None
