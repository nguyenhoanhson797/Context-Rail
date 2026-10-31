"""Budgets: what a run used, what an item has left, and which items are too long.

Usage is never zero-filled. A counter a run did not report is unknown, and an
incomplete total is reported beside its unknown count instead of being summed as
if it were complete. Unknown usage never blocks on its own, and never hides the
attempts and time that were measured locally.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from context_rail.kernel.structure import FINISHED_CATEGORIES
from context_rail.model.cto import USAGE_KEYS
from context_rail.model.primitives import ROLES

__all__ = [
    "STATES",
    "USAGE_COUNTERS",
    "ATTEMPT_TOKEN_COUNTERS",
    "WORKER_ROLES",
    "AttemptUsage",
    "Budget",
    "BudgetState",
    "ItemFacts",
    "RunFacts",
    "UsageTotals",
    "budget_state",
    "launch_refusal",
    "too_long",
    "usage_totals",
]

UNKNOWN = "unknown"
STATES = ("ok", "near", "exceeded")
USAGE_COUNTERS = USAGE_KEYS
ATTEMPT_TOKEN_COUNTERS = ("input_tokens", "output_tokens")
WORKER_ROLES = (ROLES[1], ROLES[2])
#: The role that spends the launch budget; a review is an attempt's outcome.
IMPLEMENTER_ROLE = ROLES[1]


@dataclass(frozen=True)
class AttemptUsage:
    """One attempt's usage; cache counters are folded in by the caller."""

    input_tokens: int | float | str
    output_tokens: int | float | str
    factory_credits: int | float | str = UNKNOWN


@dataclass(frozen=True)
class UsageTotals:
    """Known sums per counter, with the unknown counts kept apart."""

    known_sum: dict[str, int | float]
    unknown_attempts: dict[str, int]


@dataclass(frozen=True)
class RunFacts:
    """What one run used, measured by the caller."""

    item: str | None
    role: str
    attempts: int
    seconds: float
    usage: tuple[AttemptUsage, ...]
    failed: bool = False
    run_id: str | None = None


@dataclass(frozen=True)
class Budget:
    """A cap; ``None`` means no numeric limit."""

    attempts: int | None
    seconds: float | None
    tokens: int | None
    credits: int | None


@dataclass(frozen=True)
class BudgetState:
    """One item's budget position, its reasons and its unknown counters."""

    state: str
    reasons: tuple[str, ...]
    unknown: dict[str, int]


@dataclass(frozen=True)
class ItemFacts:
    """The slice of an item the budget rules need."""

    slug: str
    category: str
    last_activity_s: float
    dependencies: tuple[str, ...]


def usage_totals(attempts: Sequence[dict[str, Any]]) -> UsageTotals:
    """Sum every known counter and count the attempts that reported unknown."""
    known: dict[str, int | float] = {counter: 0 for counter in USAGE_COUNTERS}
    unknown: dict[str, int] = {counter: 0 for counter in USAGE_COUNTERS}
    for attempt in attempts:
        raw = attempt.get("usage") if isinstance(attempt, dict) else None
        usage = raw if isinstance(raw, dict) else {}
        for counter in USAGE_COUNTERS:
            number = _known_number(usage.get(counter, UNKNOWN))
            if number is None:
                unknown[counter] += 1
            else:
                known[counter] += number
    return UsageTotals(known_sum=known, unknown_attempts=unknown)


def budget_state(
    runs: Sequence[RunFacts], budget: Budget, near_ratio: float
) -> BudgetState:
    """The item's budget state: exceeded only strictly above a non-null cap."""
    tokens, unknown_tokens = _token_usage(runs)
    credits, unknown_credits = _credit_usage(runs)
    unknown: dict[str, int] = {}
    if unknown_tokens:
        unknown["tokens"] = unknown_tokens
    if unknown_credits:
        unknown["credits"] = unknown_credits
    used_attempts = sum(run.attempts for run in runs)
    used_seconds = sum(run.seconds for run in runs)
    state = "ok"
    reasons: list[str] = []
    for label, used, cap in (
        ("attempts", used_attempts, budget.attempts),
        ("seconds", used_seconds, budget.seconds),
        ("tokens", tokens, budget.tokens),
        ("credits", credits, budget.credits),
    ):
        state, reason = _measure(state, label, used, cap, near_ratio)
        if reason is not None:
            reasons.append(reason)
    return BudgetState(state, tuple(reasons), unknown)


def launch_refusal(runs: Sequence[RunFacts], budget: Budget) -> str | None:
    """Refuse a new attempt when no implementer attempt or time budget remains.

    Only implementer runs spend the launch budget: a review is the outcome of an
    attempt, and dispatching again after one is the loop's intended next step.
    The budget *state* and the too-long report still count both worker roles.
    """
    spent = [run for run in runs if run.role == IMPLEMENTER_ROLE]
    used_attempts = sum(run.attempts for run in spent)
    if budget.attempts is not None and used_attempts >= budget.attempts:
        return f"no attempts remain: {_fmt(used_attempts)} of {_fmt(budget.attempts)} used"
    used_seconds = sum(run.seconds for run in spent)
    if budget.seconds is not None and used_seconds >= budget.seconds:
        return f"no time remains: {_fmt(used_seconds)} of {_fmt(budget.seconds)} seconds used"
    return None


def too_long(
    items: Sequence[ItemFacts], runs: Sequence[RunFacts], budgets: dict[str, Any]
) -> tuple[str, ...]:
    """Open and blocked items whose worker attempts or runtime exceed the item cap."""
    caps = _item_caps(budgets)
    attempt_cap = caps.get("attempts")
    second_cap = caps.get("seconds")
    if attempt_cap is None and second_cap is None:
        return ()
    attempts: dict[str, int] = {}
    seconds: dict[str, float] = {}
    for run in runs:
        if run.role not in WORKER_ROLES or run.item is None:
            continue
        attempts[run.item] = attempts.get(run.item, 0) + run.attempts
        seconds[run.item] = seconds.get(run.item, 0.0) + run.seconds
    long_items = [
        item.slug
        for item in items
        if item.category not in FINISHED_CATEGORIES
        and (
            (attempt_cap is not None and attempts.get(item.slug, 0) > attempt_cap)
            or (second_cap is not None and seconds.get(item.slug, 0.0) > second_cap)
        )
    ]
    return tuple(sorted(long_items))


def _token_usage(runs: Sequence[RunFacts]) -> tuple[int | float, int]:
    known: int | float = 0
    unknown = 0
    for run in runs:
        for attempt in run.usage:
            total = 0
            missing = False
            for counter in ATTEMPT_TOKEN_COUNTERS:
                number = _known_number(getattr(attempt, counter))
                if number is None:
                    missing = True
                else:
                    total += number
            if missing:
                unknown += 1
            else:
                known += total
    return known, unknown


def _credit_usage(runs: Sequence[RunFacts]) -> tuple[int | float, int]:
    known: int | float = 0
    unknown = 0
    for run in runs:
        for attempt in run.usage:
            number = _known_number(attempt.factory_credits)
            if number is None:
                unknown += 1
            else:
                known += number
    return known, unknown


def _measure(
    state: str, label: str, used: int | float, cap: int | float | None, near_ratio: float
) -> tuple[str, str | None]:
    if cap is None:
        return state, None
    if used > cap:
        return "exceeded", f"{label} {_fmt(used)} exceed the budget of {_fmt(cap)}"
    if used >= cap * near_ratio:
        return ("near" if state == "ok" else state), f"{label} {_fmt(used)} of {_fmt(cap)} are used"
    return state, None


def _known_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _item_caps(budgets: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(budgets, dict):
        return {}
    caps = budgets.get("item")
    if isinstance(caps, dict):
        return caps
    return {key: budgets[key] for key in ("attempts", "seconds") if key in budgets}


def _fmt(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)
