"""Budgets: attempts, time and usage, where unknown usage is never zero."""

from __future__ import annotations

import pytest

from context_rail.kernel.budgets import (
    AttemptUsage,
    Budget,
    BudgetState,
    ItemFacts,
    RunFacts,
    UsageTotals,
    budget_state,
    launch_refusal,
    too_long,
    usage_totals,
)

ITEM_CAPS = Budget(attempts=4, seconds=7200, tokens=None, credits=None)
CHARTER_BUDGETS = {
    "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
    "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
}


def run(
    attempts: int = 1,
    seconds: float = 10.0,
    *,
    item: str | None = "login",
    role: str = "implementer",
    usage: tuple[AttemptUsage, ...] = (),
    failed: bool = False,
    run_id: str | None = None,
) -> RunFacts:
    return RunFacts(item, role, attempts, seconds, usage, failed=failed, run_id=run_id)


def item(slug: str, category: str = "active") -> ItemFacts:
    return ItemFacts(slug, category, 0.0, ())


def test_ok_below_the_near_ratio():
    assert budget_state((run(3),), ITEM_CAPS, 0.8) == BudgetState("ok", (), {})


def test_near_at_the_cap_but_not_exceeded():
    state = budget_state((run(4),), ITEM_CAPS, 0.8)
    assert state.state == "near" and state.reasons


def test_exceeded_only_strictly_above_the_cap():
    runs = (run(4),)
    assert budget_state(runs, Budget(4, 7200, None, None), 0.8).state == "near"
    assert budget_state(runs, Budget(3, 7200, None, None), 0.8).state == "exceeded"
    assert budget_state((run(5),), ITEM_CAPS, 0.8).state == "exceeded"


@pytest.mark.parametrize(("attempts", "state"), [(7, "ok"), (8, "near"), (10, "near"), (11, "exceeded")])
def test_near_ratio_boundary(attempts, state):
    assert budget_state((run(attempts),), Budget(10, None, None, None), 0.8).state == state


def test_launch_refusal_names_the_attempt_budget():
    reason = launch_refusal((run(4),), ITEM_CAPS)
    assert reason is not None and "attempt" in reason


def test_launch_refusal_when_the_time_is_used_up():
    reason = launch_refusal((run(1, 7200.0),), ITEM_CAPS)
    assert reason is not None and ("second" in reason or "time" in reason)


def test_launch_allows_a_run_with_remaining_budget():
    assert launch_refusal((run(3, 100.0),), ITEM_CAPS) is None


def test_no_caps_is_always_ok():
    runs = (run(99, 10_000.0),)
    assert budget_state(runs, Budget(None, None, None, None), 0.8).state == "ok"
    assert launch_refusal(runs, Budget(None, None, None, None)) is None


def test_unknown_tokens_never_flip_the_state():
    usage = (AttemptUsage("unknown", "unknown"),)
    state = budget_state((run(usage=usage), run(usage=usage)), Budget(4, 7200, 1000, None), 0.8)
    assert state.state == "ok"
    assert state.unknown["tokens"] == 2


def test_unknown_tokens_do_not_erase_attempt_enforcement():
    usage = (AttemptUsage("unknown", "unknown"),)
    state = budget_state((run(5, usage=usage),), Budget(4, 7200, 1000, None), 0.8)
    assert state.state == "exceeded"


def test_known_tokens_over_the_cap_are_exceeded():
    usage = (AttemptUsage(60, 50),)
    state = budget_state((run(1, usage=usage),), Budget(4, 7200, 100, None), 0.8)
    assert state.state == "exceeded"


def test_seconds_over_the_cap_are_exceeded():
    assert budget_state((run(1, 7201.0),), ITEM_CAPS, 0.8).state == "exceeded"


def test_credits_over_the_cap_are_exceeded():
    usage = (AttemptUsage(1, 1, 5),)
    assert budget_state((run(1, usage=usage),), Budget(4, 7200, None, 4), 0.8).state == "exceeded"


def test_usage_totals_keeps_known_sums_and_unknown_counts():
    totals = usage_totals(
        [
            {"usage": {"input_tokens": 100, "factory_credits": 0}},
            {"usage": {"input_tokens": "unknown", "factory_credits": 0}},
            {"usage": {"input_tokens": 50, "factory_credits": 0}},
        ]
    )
    assert isinstance(totals, UsageTotals)
    assert totals.known_sum["input_tokens"] == 150
    assert totals.unknown_attempts["input_tokens"] == 1
    assert totals.known_sum["factory_credits"] == 0
    assert totals.unknown_attempts["factory_credits"] == 0


def test_usage_totals_missing_counters_are_unknown_not_zero():
    totals = usage_totals([{"usage": {"input_tokens": "unknown", "output_tokens": 2}}])
    assert totals.known_sum["output_tokens"] == 2
    assert totals.unknown_attempts["input_tokens"] == 1
    assert totals.unknown_attempts["cache_read_input_tokens"] == 1
    assert totals.unknown_attempts["factory_credits"] == 1


def test_usage_totals_sums_every_known_counter():
    totals = usage_totals(
        [
            {
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 4,
                    "factory_credits": 5,
                }
            }
        ]
    )
    assert totals.known_sum == {
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 4,
        "factory_credits": 5,
    }
    assert set(totals.unknown_attempts.values()) == {0}


@pytest.mark.parametrize("value", ["unknown", True, False, None, -1, -0.5, "12", float("nan"), float("inf")])
def test_usage_totals_treats_malformed_values_as_unknown(value):
    totals = usage_totals([{"usage": {"input_tokens": value}}])
    assert totals.unknown_attempts["input_tokens"] == 1
    assert totals.known_sum["input_tokens"] == 0


def test_usage_totals_survives_an_attempt_without_usage():
    totals = usage_totals([{}, {"usage": None}])
    assert totals.unknown_attempts["input_tokens"] == 2


def test_too_long_sums_worker_attempts_for_a_blocked_item():
    items = (item("login", "blocked"), item("other"), item("clean"))
    runs = (
        run(3, item="login", role="implementer"),
        run(2, item="login", role="reviewer"),
        run(1, item="other"),
    )
    assert too_long(items, runs, CHARTER_BUDGETS) == ("login",)


def test_too_long_ignores_cto_overhead_and_finished_items():
    items = (item("login"), item("shipped", "done"), item("dropped", "dropped"))
    runs = (
        run(90, item="login", role="cto"),
        run(90, item="shipped"),
        run(90, item="dropped"),
    )
    assert too_long(items, runs, CHARTER_BUDGETS) == ()


def test_too_long_boundary_at_the_cap_is_not_too_long():
    items = (item("login"),)
    runs = (run(4, item="login"),)
    assert too_long(items, runs, CHARTER_BUDGETS) == ()
    assert too_long(items, (run(5, item="login"),), CHARTER_BUDGETS) == ("login",)


def test_too_long_honours_the_time_cap():
    items = (item("login"), item("other"))
    runs = (run(1, 7201.0, item="login"), run(1, 7199.0, item="other"))
    assert too_long(items, runs, CHARTER_BUDGETS) == ("login",)


def test_too_long_without_a_cap_is_empty():
    items = (item("login"),)
    runs = (run(90, 90_000.0, item="login"),)
    assert too_long(items, runs, {}) == ()
    assert too_long(items, runs, {"item": {"attempts": None, "seconds": None}}) == ()


def test_usage_vocabulary_is_the_model_vocabulary() -> None:
    from context_rail.kernel.budgets import USAGE_COUNTERS
    from context_rail.model.cto import USAGE_KEYS

    assert USAGE_COUNTERS is USAGE_KEYS


def test_a_non_worker_role_does_not_spend_the_item_budget() -> None:
    from context_rail.kernel.budgets import AttemptUsage, Budget, RunFacts, budget_state

    worker = RunFacts(
        item="login",
        role="implementer",
        attempts=1,
        seconds=50.0,
        usage=(AttemptUsage(1, 1, 1),),
        failed=False,
        run_id="run-1",
    )
    tick = RunFacts(
        item="login",
        role="cto",
        attempts=99,
        seconds=9999.0,
        usage=(AttemptUsage(9999, 9999, 9999),),
        failed=False,
        run_id="tick-1",
    )

    state = budget_state([worker, tick], Budget(2, 60.0, 10, 10), 0.8)

    assert state.state == "near"
    assert not any("exceed" in reason for reason in state.reasons)
