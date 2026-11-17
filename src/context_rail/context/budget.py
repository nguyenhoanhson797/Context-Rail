"""The budget position of an item, read from the candidates recorded for it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from context_rail.kernel import budgets
from context_rail.kernel.structure import FINISHED_CATEGORIES
from context_rail.store import query
from context_rail.store.index import Index
from context_rail.store.measure import effort_samples

__all__ = ["NEAR_RATIO_DEFAULT", "item_budget", "item_caps", "items_over_budget", "near_ratio"]

NEAR_RATIO_DEFAULT = 0.8


def near_ratio(policy: Any) -> float:
    if policy is None:
        return NEAR_RATIO_DEFAULT
    return float(policy.thresholds.get("near_budget_ratio", NEAR_RATIO_DEFAULT))


def item_caps(policy: Any, extra: Mapping[str, Any] | None = None) -> budgets.Budget:
    """The charter's item caps, tightened by any cap a profile also sets."""
    scope = {} if policy is None else policy.budgets.get("item", {})
    caps: dict[str, int | float | None] = dict(scope)
    for name, cap in (extra or {}).items():
        if cap is None:
            continue
        current = caps.get(name)
        caps[name] = cap if current is None else min(current, cap)
    return budgets.Budget(caps)


def _effort_facts(index: Index, slug: str | None = None) -> list[budgets.CandidateFacts]:
    """Joined candidate and result effort, as the budget rules read it."""
    samples, _ = effort_samples(index, item=slug)
    return [
        budgets.CandidateFacts(sample.item or "", sample.duration_s, sample.usage)
        for sample in samples
    ]


def item_budget(
    index: Index, policy: Any, slug: str, extra: Mapping[str, Any] | None = None
) -> budgets.BudgetState:
    """Where one item stands against its caps; only a warning, never a refusal."""
    facts = query.candidate_facts(index, slug)
    return budgets.budget_state(
        facts,
        item_caps(policy, extra),
        near_ratio(policy),
        effort=_effort_facts(index, slug),
    )


def items_over_budget(index: Index, policy: Any) -> dict[str, budgets.BudgetState]:
    """Every unfinished item whose recorded effort exceeds a cap, with its state."""
    caps = item_caps(policy)
    if not any(cap is not None for cap in caps.caps.values()):
        return {}
    grouped: dict[str, list[budgets.CandidateFacts]] = {}
    for fact in query.candidate_facts(index):
        grouped.setdefault(fact.item, []).append(fact)
    effort: dict[str, list[budgets.CandidateFacts]] = {}
    for fact in _effort_facts(index):
        effort.setdefault(fact.item, []).append(fact)
    for slug in effort:
        grouped.setdefault(slug, [])
    marks = ",".join("?" for _ in FINISHED_CATEGORIES)
    finished = {
        slug
        for (slug,) in index.conn.execute(
            "SELECT slug FROM docs WHERE primitive = 'item' AND broken = 0 "
            f"AND category IN ({marks})",
            tuple(FINISHED_CATEGORIES),
        )
    }
    ratio = near_ratio(policy)
    states = {
        slug: budgets.budget_state(facts, caps, ratio, effort=effort.get(slug, []))
        for slug, facts in grouped.items()
        if slug not in finished
    }
    return {slug: state for slug, state in sorted(states.items()) if state.state == "exceeded"}