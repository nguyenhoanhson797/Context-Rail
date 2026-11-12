"""Budgets: attempts, time and usage counters, where unknown usage is never zero."""

from __future__ import annotations

import pytest

from context_rail.kernel import budgets
from context_rail.kernel.budgets import (
    Budget,
    BudgetState,
    CandidateFacts,
    ItemFacts,
    UsageTotals,
    budget_state,
    too_long,
    usage_totals,
)

ITEM_CAPS = Budget({"attempts": 4, "seconds": 7200})
CHARTER_BUDGETS = {
    "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
    "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
}


def cand(
    item: str = "login", seconds: float | None = 10.0, **usage: int | float | str
) -> CandidateFacts:
    return CandidateFacts(item, seconds, usage)


def many(count: int, item: str = "login", seconds: float | None = 10.0) -> tuple[CandidateFacts, ...]:
    return tuple(cand(item, seconds) for _ in range(count))


def plan_item(slug: str, category: str = "active") -> ItemFacts:
    return ItemFacts(slug, category, 0.0, ())


def test_ok_below_the_near_ratio():
    assert budget_state(many(3), ITEM_CAPS, 0.8) == BudgetState("ok", (), {})


def test_near_at_the_cap_but_not_exceeded():
    state = budget_state(many(4), ITEM_CAPS, 0.8)
    assert state.state == "near" and state.reasons


def test_budget_counts_candidates_as_attempts():
    assert budget_state(many(3), Budget({"attempts": 2}), 0.8).state == "exceeded"
    assert budget_state(many(2), Budget({"attempts": 2}), 0.8).state == "near"


@pytest.mark.parametrize(("attempts", "state"), [(7, "ok"), (8, "near"), (10, "near"), (11, "exceeded")])
def test_near_ratio_boundary(attempts, state):
    assert budget_state(many(attempts), Budget({"attempts": 10}), 0.8).state == state


def test_no_caps_is_always_ok():
    assert budget_state(many(99, seconds=10_000.0), Budget({}), 0.8).state == "ok"
    assert budget_state(many(99), Budget({"attempts": None, "tokens": None}), 0.8).state == "ok"


def test_seconds_over_the_cap_are_exceeded():
    assert budget_state((cand(seconds=7201.0),), ITEM_CAPS, 0.8).state == "exceeded"


def test_budget_caps_a_named_usage_counter():
    candidates = (cand(input_tokens=70), cand(input_tokens=50))
    state = budget_state(candidates, Budget({"input_tokens": 100}), 0.8)
    assert state.state == "exceeded"
    assert "input_tokens" in state.reasons[0]


def test_budget_tokens_cap_reads_counter_named_tokens():
    assert budget_state((cand(tokens=120),), Budget({"tokens": 100}), 0.8).state == "exceeded"
    assert budget_state((cand(input_tokens=120),), Budget({"tokens": 100}), 0.8).state == "ok"


def test_budget_unknown_usage_kept_apart():
    candidates = (cand(input_tokens="unknown"), cand(input_tokens="unknown"), cand())
    state = budget_state(candidates, Budget({"input_tokens": 1000}), 0.8)
    assert state.state == "ok"
    assert state.unknown == {"input_tokens": 3}


def test_unknown_usage_does_not_erase_attempt_enforcement():
    candidates = tuple(cand(input_tokens="unknown") for _ in range(5))
    state = budget_state(candidates, Budget({"attempts": 4, "input_tokens": 1000}), 0.8)
    assert state.state == "exceeded"
    assert state.unknown == {"input_tokens": 5}


def test_a_candidate_without_a_duration_leaves_seconds_unknown():
    state = budget_state((cand(seconds=None), cand(seconds=5.0)), Budget({"seconds": 100}), 0.8)
    assert state.state == "ok"
    assert state.unknown == {"seconds": 1}


def test_launch_refusal_is_gone():
    assert not hasattr(budgets, "launch_refusal")
    assert not hasattr(budgets, "RunFacts")
    assert not hasattr(budgets, "USAGE_COUNTERS")


def test_usage_totals_keeps_known_sums_and_unknown_counts():
    totals = usage_totals(
        [
            {"input_tokens": 100, "cost": 0},
            {"input_tokens": "unknown", "cost": 0},
            {"input_tokens": 50, "cost": 0},
        ]
    )
    assert isinstance(totals, UsageTotals)
    assert totals.known_sum == {"input_tokens": 150, "cost": 0}
    assert totals.unknown_attempts == {"input_tokens": 1, "cost": 0}


def test_usage_totals_missing_counters_are_unknown_not_zero():
    totals = usage_totals([{"input_tokens": "unknown", "output_tokens": 2}, {"input_tokens": 4}])
    assert totals.known_sum == {"input_tokens": 4, "output_tokens": 2}
    assert totals.unknown_attempts == {"input_tokens": 1, "output_tokens": 1}


@pytest.mark.parametrize("value", ["unknown", True, False, None, -1, -0.5, "12", float("nan"), float("inf")])
def test_usage_totals_treats_malformed_values_as_unknown(value):
    totals = usage_totals([{"input_tokens": value}])
    assert totals.unknown_attempts["input_tokens"] == 1
    assert totals.known_sum["input_tokens"] == 0


def test_usage_totals_of_nothing_is_empty():
    totals = usage_totals([{}, {}])
    assert totals.known_sum == {} and totals.unknown_attempts == {}


def test_too_long_counts_candidates_for_a_blocked_item():
    items = (plan_item("login", "blocked"), plan_item("other"), plan_item("clean"))
    candidates = (*many(5, "login"), *many(1, "other"))
    assert too_long(items, candidates, CHARTER_BUDGETS) == ("login",)


def test_too_long_ignores_finished_items():
    items = (plan_item("login"), plan_item("shipped", "done"), plan_item("dropped", "dropped"))
    candidates = (*many(1, "login"), *many(90, "shipped"), *many(90, "dropped"))
    assert too_long(items, candidates, CHARTER_BUDGETS) == ()


def test_too_long_boundary_at_the_cap_is_not_too_long():
    items = (plan_item("login"),)
    assert too_long(items, many(4), CHARTER_BUDGETS) == ()
    assert too_long(items, many(5), CHARTER_BUDGETS) == ("login",)


def test_too_long_honours_the_time_cap():
    items = (plan_item("login"), plan_item("other"))
    candidates = (cand("login", 7201.0), cand("other", 7199.0))
    assert too_long(items, candidates, CHARTER_BUDGETS) == ("login",)


def test_too_long_without_a_cap_is_empty():
    items = (plan_item("login"),)
    candidates = many(90, seconds=90_000.0)
    assert too_long(items, candidates, {}) == ()
    assert too_long(items, candidates, {"item": {"attempts": None, "seconds": None}}) == ()