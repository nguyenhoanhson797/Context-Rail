"""Budgets: what an item's candidates used, what is left, and which items run long.

Usage is never zero-filled. A counter a candidate did not report is unknown, and
an incomplete total is reported beside its unknown count instead of being summed
as if it were complete. Unknown usage never blocks on its own, and never hides
the attempts and time that were measured locally. A budget only warns: nothing
here refuses work.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from context_rail.kernel.structure import FINISHED_CATEGORIES

__all__ = [
    "STATES",
    "Budget",
    "BudgetState",
    "CandidateFacts",
    "ItemFacts",
    "UsageTotals",
    "budget_state",
    "too_long",
    "usage_totals",
]

UNKNOWN = "unknown"
STATES = ("ok", "near", "exceeded")
#: Caps measured from the candidate count and recorded durations, not a counter.
_LOCAL_CAPS = ("attempts", "seconds")


@dataclass(frozen=True)
class CandidateFacts:
    """What one recorded candidate says it cost."""

    item: str
    duration_s: float | None
    usage: Mapping[str, int | float | str]


@dataclass(frozen=True)
class UsageTotals:
    """Known sums per counter, with the unknown counts kept apart."""

    known_sum: dict[str, int | float]
    unknown_attempts: dict[str, int]


@dataclass(frozen=True)
class Budget:
    """Caps by name: ``attempts``, ``seconds`` or a usage counter; ``None`` is no limit."""

    caps: Mapping[str, int | float | None]


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


def usage_totals(usages: Sequence[Mapping[str, Any]]) -> UsageTotals:
    """Sum every known counter and count the usages that reported it unknown."""
    named: list[str] = []
    maps = [usage if isinstance(usage, Mapping) else {} for usage in usages]
    for usage in maps:
        for key in usage:
            if key not in named:
                named.append(key)
    known: dict[str, int | float] = {key: 0 for key in named}
    unknown: dict[str, int] = {key: 0 for key in named}
    for usage in maps:
        for key in named:
            number = _known_number(usage.get(key, UNKNOWN))
            if number is None:
                unknown[key] += 1
            else:
                known[key] += number
    return UsageTotals(known_sum=known, unknown_attempts=unknown)


def budget_state(
    candidates: Sequence[CandidateFacts],
    budget: Budget,
    near_ratio: float,
    *,
    effort: Sequence[CandidateFacts] | None = None,
) -> BudgetState:
    """The item's budget state: exceeded only strictly above a non-null cap.

    ``attempts`` counts candidates, so a retry of code work keeps its meaning.
    Time and usage counters sum ``effort`` when it is given, which also covers
    work that produced no commit.
    """
    spent = candidates if effort is None else effort
    state = "ok"
    reasons: list[str] = []
    unknown: dict[str, int] = {}
    for name, cap in budget.caps.items():
        if cap is None:
            continue
        if name == "attempts":
            used: int | float = len(candidates)
        elif name == "seconds":
            durations = [_known_number(item.duration_s) for item in spent]
            used = sum(value for value in durations if value is not None)
            missing = sum(1 for value in durations if value is None)
            if missing:
                unknown[name] = missing
        else:
            counters = [_known_number(item.usage.get(name, UNKNOWN)) for item in spent]
            used = sum(value for value in counters if value is not None)
            missing = sum(1 for value in counters if value is None)
            if missing:
                unknown[name] = missing
        state, reason = _measure(state, name, used, cap, near_ratio)
        if reason is not None:
            reasons.append(reason)
    return BudgetState(state, tuple(reasons), unknown)


def too_long(
    items: Sequence[ItemFacts],
    candidates: Sequence[CandidateFacts],
    budgets: Mapping[str, Any],
) -> tuple[str, ...]:
    """Open and blocked items whose candidates or recorded time exceed the item cap."""
    caps = _item_caps(budgets)
    attempt_cap = caps.get("attempts")
    second_cap = caps.get("seconds")
    if attempt_cap is None and second_cap is None:
        return ()
    attempts: dict[str, int] = {}
    seconds: dict[str, float] = {}
    for candidate in candidates:
        attempts[candidate.item] = attempts.get(candidate.item, 0) + 1
        spent = _known_number(candidate.duration_s)
        if spent is not None:
            seconds[candidate.item] = seconds.get(candidate.item, 0.0) + spent
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


def _item_caps(budgets: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(budgets, Mapping):
        return {}
    caps = budgets.get("item")
    if isinstance(caps, Mapping):
        return caps
    return {key: budgets[key] for key in _LOCAL_CAPS if key in budgets}


def _fmt(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)