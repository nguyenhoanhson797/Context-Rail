"""Gates: whether a target may advance, from the proofs the caller supplies.

A gate never passes on an empty, stale or unknown proof set, and never on a
review that is bound to another commit. The caller supplies the selected
Checks (a scope gate's selection includes its descendants' rollup) and a review
that was already checked with :func:`context_rail.kernel.rights.separation_problem`;
when the review carries the implementer facts, this module checks that too. A
candidate commit is required only when the gate asks for a review or a selected
Check is bound to a commit, so a gate that needs a human alone opens without one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from context_rail.kernel.rights import separation_problem
from context_rail.model.policy import Charter

__all__ = [
    "FRESHNESS",
    "CheckState",
    "GateDecision",
    "ResultState",
    "ReviewState",
    "evaluate_gate",
]

FRESHNESS = ("fresh", "stale", "unknown", "missing", "untracked")
APPROVED = "approve"


@dataclass(frozen=True)
class CheckState:
    """One selected Check's last proof, as the caller read it."""

    slug: str
    passed: bool
    freshness: str
    candidate: str | None
    acceptance: bool
    requires_candidate: bool = False


@dataclass(frozen=True)
class ResultState:
    """Current producer result evidence for one item, as read from the store."""

    record: str | None
    body_hash: str | None
    current: bool
    completed: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewState:
    """The reviewer's verdict on one candidate commit and/or producer result."""

    verdict: str
    candidate: str | None
    reviewer: str
    model: str | None = None
    implementer: str | None = None
    implementer_model: str | None = None
    result_record: str | None = None
    result_body_hash: str | None = None
    current: bool = True
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateDecision:
    """Whether the gate opens, and every reason it stayed shut."""

    allowed: bool
    reasons: tuple[str, ...]


def evaluate_gate(
    policy: Charter,
    gate: str,
    target: str,
    checks: Sequence[CheckState],
    review: ReviewState | None,
    human: bool,
    candidate: str | None,
    *,
    result_required: bool = False,
    result: ResultState | None = None,
) -> GateDecision:
    """Decide one gate; an unknown gate, freshness, missing candidate or missing result blocks."""
    if gate not in policy.gates:
        return GateDecision(False, (f"unknown gate {gate!r}",))
    rule = policy.gates[gate]
    selected = tuple(checks)
    reasons: list[str] = []
    if result_required:
        reasons.extend(_result_reasons(result))
    has_result_backed_review = (
        result_required
        and review is not None
        and review.result_record is not None
    )
    if not candidate and (
        any(check.requires_candidate for check in selected)
        or (rule.require_review and not has_result_backed_review)
    ):
        reasons.append("no candidate commit was supplied")
    if gate == "scope_done" and target not in policy.project.get("scope", ()):
        reasons.append(f"{target!r} is not a declared scope entry")
    for check in selected:
        if check.freshness not in FRESHNESS:
            reasons.append(f"check {check.slug!r} has unknown freshness {check.freshness!r}")
    if rule.require_checks:
        if not selected:
            reasons.append("no checks were selected; an empty required set never passes")
        else:
            reasons.extend(_check_reasons(selected, candidate))
    if rule.require_acceptance and not _has_fresh_acceptance(selected):
        reasons.append("no acceptance check passed fresh")
    if rule.require_review:
        reasons.extend(
            _review_reasons(
                policy,
                review,
                candidate,
                result_required=result_required,
                result=result,
            )
        )
    if rule.require_human and not human:
        reasons.append("a human verdict or approval is required")
    return GateDecision(not reasons, tuple(reasons))


def _result_reasons(result: ResultState | None) -> list[str]:
    if result is None or not result.record:
        return ["no current completed producer result was recorded"]
    if not result.current or not result.completed:
        if result.reasons:
            return list(result.reasons)
        if not result.current:
            return [f"producer result {result.record!r} is not current"]
        return [f"producer result {result.record!r} is not completed"]
    return []


def _check_reasons(checks: tuple[CheckState, ...], candidate: str | None) -> list[str]:
    reasons: list[str] = []
    for check in checks:
        if check.freshness != "fresh":
            reasons.append(f"check {check.slug!r} is {check.freshness!r}, not fresh")
        if not check.passed:
            reasons.append(f"check {check.slug!r} did not pass")
        if check.candidate and candidate and check.candidate != candidate:
            reasons.append(f"check {check.slug!r} was proven against {check.candidate}, not {candidate}")
        if check.requires_candidate and not check.candidate:
            reasons.append(
                f"check {check.slug!r} is not bound to the candidate commit"
            )
    return reasons


def _has_fresh_acceptance(checks: tuple[CheckState, ...]) -> bool:
    return any(
        check.acceptance and check.passed and check.freshness == "fresh" for check in checks
    )


def _review_reasons(
    policy: Charter,
    review: ReviewState | None,
    candidate: str | None,
    *,
    result_required: bool = False,
    result: ResultState | None = None,
) -> list[str]:
    if review is None:
        return ["no review was recorded"]
    reasons: list[str] = []
    if review.verdict != APPROVED:
        reasons.append(f"the review verdict is {review.verdict!r}, not {APPROVED!r}")
    if not review.current:
        if review.reasons:
            reasons.extend(review.reasons)
        else:
            reasons.append("the review is no longer current")
    if result_required:
        if not review.result_record:
            reasons.append("the review is not bound to a producer result")
        elif result is not None and result.record is not None:
            if review.result_record != result.record:
                reasons.append(
                    f"the review approved result {review.result_record}, not {result.record}"
                )
            elif (
                result.body_hash is not None
                and review.result_body_hash != result.body_hash
            ):
                reasons.append(
                    "the review result_body_hash does not match the current result"
                )
        if candidate:
            if not review.candidate:
                reasons.append("the review is not bound to a candidate commit")
            elif review.candidate != candidate:
                reasons.append(f"the review approved {review.candidate}, not {candidate}")
        elif review.candidate:
            reasons.append(
                f"the review approved candidate {review.candidate}, but no candidate is current"
            )
    else:
        if not review.candidate:
            reasons.append("the review is not bound to a candidate commit")
        elif review.candidate != candidate:
            reasons.append(f"the review approved {review.candidate}, not {candidate}")
    if not review.reviewer:
        reasons.append("the review has no reviewer")
    if review.implementer or review.implementer_model:
        problem = separation_problem(
            review.implementer or "",
            review.reviewer,
            review.implementer_model,
            review.model,
            policy.reviewer_different_model,
        )
        if problem is not None:
            reasons.append(problem[1])
    elif policy.reviewer_different_model and not review.model:
        reasons.append("the review names no reviewer model, and the charter requires a different one")
    return reasons
