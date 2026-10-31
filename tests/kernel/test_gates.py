"""Gates: item done, scope done, merge and release never pass on weak proof."""

from __future__ import annotations

from dataclasses import replace

import pytest

from context_rail.kernel.gates import (
    CheckState,
    GateDecision,
    ReviewState,
    evaluate_gate,
)

REVIEWER_MODEL = "custom:gemini-3.8-flash-LTN-0"


def fresh(slug: str = "unit", acceptance: bool = False, candidate: str | None = "c1") -> CheckState:
    return CheckState(slug, True, "fresh", candidate, acceptance)


def approving(candidate: str = "c1") -> ReviewState:
    return ReviewState("approve", candidate, "reviewer-1", REVIEWER_MODEL)


def test_empty_required_checks_blocks(policy):
    decision = evaluate_gate(policy, "item_done", "login", [], None, False, "c1")
    assert decision.allowed is False
    assert decision.reasons


@pytest.mark.parametrize("freshness", ["stale", "unknown", "missing", "Fresh", ""])
def test_every_non_fresh_check_blocks(policy, freshness):
    checks = [CheckState("unit", True, freshness, "c1", False)]
    decision = evaluate_gate(policy, "item_done", "login", checks, approving(), False, "c1")
    assert decision.allowed is False
    assert any("unit" in reason for reason in decision.reasons)


def test_a_failed_check_blocks_even_when_fresh(policy):
    checks = [CheckState("unit", False, "fresh", "c1", False)]
    assert evaluate_gate(policy, "item_done", "login", checks, approving(), False, "c1").allowed is False


def test_item_done_allows_a_fresh_check_and_a_bound_review(policy):
    decision = evaluate_gate(policy, "item_done", "login", [fresh()], approving(), False, "c1")
    assert decision == GateDecision(allowed=True, reasons=())


@pytest.mark.parametrize(
    "review",
    [
        None,
        ReviewState("reject", "c1", "reviewer-1", REVIEWER_MODEL),
        ReviewState("Approve", "c1", "reviewer-1", REVIEWER_MODEL),
        ReviewState("approve", "", "reviewer-1", REVIEWER_MODEL),
        ReviewState("approve", "c1", "", REVIEWER_MODEL),
        ReviewState("approve", "c1", "reviewer-1", ""),
    ],
)
def test_review_must_be_a_named_approval(policy, review):
    decision = evaluate_gate(policy, "item_done", "login", [fresh()], review, False, "c1")
    assert decision.allowed is False and decision.reasons


def test_review_must_bind_the_candidate(policy):
    review = ReviewState("approve", "other", "reviewer-1", REVIEWER_MODEL)
    decision = evaluate_gate(
        policy, "merge", "login", [CheckState("unit", True, "fresh", "c1", False)], review, False, "c1"
    )
    assert decision.allowed is False
    assert any("other" in reason for reason in decision.reasons)


def test_a_check_bound_to_another_candidate_blocks(policy):
    checks = [CheckState("unit", True, "fresh", "old", False)]
    assert evaluate_gate(policy, "item_done", "login", checks, approving(), False, "c1").allowed is False


def test_a_check_without_a_commit_binding_does_not_block(policy):
    checks = [CheckState("human-review", True, "fresh", None, False)]
    assert evaluate_gate(policy, "item_done", "login", checks, approving(), False, "c1").allowed is True


@pytest.mark.parametrize("gate", ["not_a_gate", "", "ITEM_DONE", "scope"])
def test_unknown_gate_blocks(policy, gate):
    decision = evaluate_gate(policy, gate, "login", [fresh()], approving(), True, "c1")
    assert decision.allowed is False and decision.reasons


@pytest.mark.parametrize("candidate", [None, ""])
def test_a_missing_candidate_blocks(policy, candidate):
    decision = evaluate_gate(policy, "item_done", "login", [fresh()], approving(), False, candidate)
    assert decision.allowed is False and decision.reasons


def test_scope_gate_needs_a_fresh_passing_acceptance_check(policy):
    checks = [fresh("unit")]
    assert evaluate_gate(policy, "scope_done", "must-1", checks, approving(), False, "c1").allowed is False
    failed = [fresh("unit"), CheckState("accept", False, "fresh", "c1", True)]
    assert evaluate_gate(policy, "scope_done", "must-1", failed, approving(), False, "c1").allowed is False
    stale = [fresh("unit"), CheckState("accept", True, "stale", "c1", True)]
    assert evaluate_gate(policy, "scope_done", "must-1", stale, approving(), False, "c1").allowed is False
    passing = [fresh("unit"), fresh("accept", acceptance=True)]
    assert evaluate_gate(policy, "scope_done", "must-1", passing, approving(), False, "c1").allowed is True


def test_scope_gate_needs_a_declared_scope_target(policy):
    checks = [fresh("unit"), fresh("accept", acceptance=True)]
    decision = evaluate_gate(policy, "scope_done", "not-in-scope", checks, approving(), False, "c1")
    assert decision.allowed is False
    assert any("scope" in reason for reason in decision.reasons)


def test_release_requires_a_human(policy):
    checks = [fresh("unit"), fresh("accept", acceptance=True)]
    assert evaluate_gate(policy, "release", "login", checks, approving(), False, "c1").allowed is False
    assert evaluate_gate(policy, "release", "login", checks, approving(), True, "c1").allowed is True


def test_merge_requires_checks_and_review(policy):
    assert evaluate_gate(policy, "merge", "login", [], approving(), False, "c1").allowed is False
    assert evaluate_gate(policy, "merge", "login", [fresh()], None, False, "c1").allowed is False
    assert evaluate_gate(policy, "merge", "login", [fresh()], approving(), False, "c1").allowed is True


def test_item_done_does_not_require_acceptance(policy):
    assert evaluate_gate(policy, "item_done", "login", [fresh()], approving(), False, "c1").allowed is True


def test_a_bound_review_checks_separation_when_either_implementer_fact_is_known(policy):
    same_actor = ReviewState(
        "approve", "c1", "reviewer-1", REVIEWER_MODEL, implementer="reviewer-1"
    )
    decision = evaluate_gate(policy, "item_done", "login", [fresh()], same_actor, False, "c1")
    assert decision.allowed is False
    assert any("reviewer" in reason for reason in decision.reasons)

    model_only = ReviewState(
        "approve", "c1", "reviewer-1", REVIEWER_MODEL, implementer_model=REVIEWER_MODEL
    )
    assert (
        evaluate_gate(policy, "item_done", "login", [fresh()], model_only, False, "c1").allowed
        is False
    )


def test_separation_by_actor_holds_when_the_charter_does_not_require_another_model(policy):
    optional = replace(policy, reviewer_different_model=False)
    same_actor = ReviewState(
        "approve", "c1", "reviewer-1", REVIEWER_MODEL, implementer="reviewer-1"
    )
    assert (
        evaluate_gate(optional, "item_done", "login", [fresh()], same_actor, False, "c1").allowed
        is False
    )

    other = ReviewState("approve", "c1", "reviewer-1", REVIEWER_MODEL, implementer="implementer-1")
    assert (
        evaluate_gate(optional, "item_done", "login", [fresh()], other, False, "c1").allowed
        is True
    )


def test_blocked_decisions_always_explain_themselves(policy):
    decision = evaluate_gate(policy, "item_done", "login", [], None, False, None)
    assert decision.allowed is False
    assert len(decision.reasons) >= 3
    assert all(isinstance(reason, str) and reason for reason in decision.reasons)


def test_a_candidate_check_without_a_candidate_blocks(policy) -> None:
    unbound = CheckState("unit", True, "fresh", None, False, requires_candidate=True)
    decision = evaluate_gate(
        policy, "item_done", "login", [unbound], approving(), False, "c1"
    )
    assert decision.allowed is False
    assert any("not bound" in reason for reason in decision.reasons)


def test_a_non_candidate_check_may_omit_a_candidate(policy) -> None:
    evidence = CheckState("evidence", True, "fresh", None, False, requires_candidate=False)
    decision = evaluate_gate(
        policy, "item_done", "login", [evidence], approving(), False, "c1"
    )
    assert decision.allowed is True
