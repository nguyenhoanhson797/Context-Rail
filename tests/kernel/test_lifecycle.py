"""Lifecycle rules: transitions are workflow-scoped; categories stay pack-wide."""

from __future__ import annotations

import pytest

from context_rail.kernel.lifecycle import category_of, check_transition
from context_rail.model.pack import Workflow


def _default(pack):
    return pack.workflow("default")


def test_basic_pack_transitions(basic_pack):
    assert check_transition(_default(basic_pack), "todo", "doing") is None
    assert check_transition(_default(basic_pack), "todo", "done") is not None
    assert check_transition(_default(basic_pack), "done", "nope") is not None


@pytest.mark.parametrize(
    ("current", "target"),
    [("todo", "doing"), ("todo", "dropped"), ("doing", "blocked"), ("doing", "done"),
     ("doing", "todo"), ("blocked", "doing"), ("done", "doing"), ("dropped", "todo")],
)
def test_declared_transitions_are_allowed(basic_pack, current, target):
    assert check_transition(_default(basic_pack), current, target) is None


@pytest.mark.parametrize(
    ("current", "target"),
    [("todo", "blocked"), ("doing", "doing"), ("blocked", "done"), ("done", "todo"),
     ("dropped", "done")],
)
def test_undeclared_transitions_are_refused_with_a_reason(basic_pack, current, target):
    reason = check_transition(_default(basic_pack), current, target)
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize(
    ("current", "target"),
    [("nope", "doing"), ("todo", "nope"), ("nope", "nope"), ("", "todo")],
)
def test_unknown_statuses_are_refused(basic_pack, current, target):
    reason = check_transition(_default(basic_pack), current, target)
    assert isinstance(reason, str) and reason


def test_category_of_maps_every_small_status(basic_pack):
    assert category_of(basic_pack, "todo") == "not_started"
    assert category_of(basic_pack, "doing") == "active"
    assert category_of(basic_pack, "blocked") == "blocked"
    assert category_of(basic_pack, "done") == "done"
    assert category_of(basic_pack, "dropped") == "dropped"
    assert category_of(basic_pack, "nope") is None


STATUSES = {
    "todo": "not_started",
    "doing": "active",
    "review": "active",
    "blocked": "blocked",
    "done": "done",
    "dropped": "dropped",
    "reported": "not_started",
    "fixing": "active",
    "verifying": "active",
    "closed": "done",
}

SPRINT = Workflow(
    name="sprint",
    initial="todo",
    transitions={
        "todo": ("doing", "dropped"),
        "doing": ("review", "blocked", "dropped"),
        "review": ("doing", "done", "dropped"),
        "blocked": ("doing", "dropped"),
        "done": ("doing",),
        "dropped": ("todo",),
    },
    execution={"review": {"dispatch": False}},
    quality={},
    statuses=STATUSES,
)

BUGFIX = Workflow(
    name="bugfix",
    initial="reported",
    transitions={
        "reported": ("fixing", "closed"),
        "fixing": ("verifying", "reported"),
        "verifying": ("closed", "fixing"),
        "closed": ("fixing",),
    },
    execution={"verifying": {"dispatch": False}},
    quality={},
    statuses=STATUSES,
)


def test_the_same_move_depends_on_the_workflow() -> None:
    assert check_transition(SPRINT, "doing", "review") is None
    assert check_transition(BUGFIX, "doing", "review") is not None
    assert check_transition(BUGFIX, "fixing", "verifying") is None


def test_execution_flags_come_from_the_workflow() -> None:
    assert SPRINT.mergeable("review")
    assert not SPRINT.dispatchable("review")
    assert BUGFIX.dispatchable("fixing")
    assert not BUGFIX.dispatchable("verifying")
