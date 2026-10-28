"""Gates: whether a target may advance, from the proofs the caller supplies.

A gate never passes on an empty, stale or unknown proof set, and never on a
review that is bound to another commit. The caller supplies the selected
Checks (a scope gate's selection includes its descendants' rollup) and a review
that was already checked with :func:`context_rail.kernel.rights.review_separation`;
when the review carries the implementer facts, this module checks that too.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from context_rail.kernel.rights import review_separation
from context_rail.model.policy import Charter

__all__ = ["FRESHNESS", "CheckState", "GateDecision", "ReviewState", "evaluate_gate"]

FRESHNESS = ("fresh", "stale", "unknown", "missing")
APPROVED = "approve"


@dataclass(frozen=True)
class CheckState:
    """One selected Check's last proof, as the caller read it."""

    slug: str
    passed: bool
    freshness: str
    candidate: str | None
    acceptance: bool


@dataclass(frozen=True)
class ReviewState:
    """The reviewer's verdict on one candidate commit."""

    verdict: str
    candidate: str
    reviewer: str
    model: str
    implementer: str | None = None
    implementer_model: str | None = None


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
) -> GateDecision:
    """Decide one gate; an unknown gate, freshness or missing candidate blocks."""
    if gate not in policy.gates:
        return GateDecision(False, (f"unknown gate {gate!r}",))
    rule = policy.gates[gate]
    selected = tuple(checks)
    reasons: list[str] = []
    if not candidate:
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
        reasons.extend(_review_reasons(policy, review, candidate))
    if rule.require_human and not human:
        reasons.append("a human verdict or approval is required")
    return GateDecision(not reasons, tuple(reasons))


def _check_reasons(checks: tuple[CheckState, ...], candidate: str | None) -> list[str]:
    reasons: list[str] = []
    for check in checks:
        if check.freshness != "fresh":
            reasons.append(f"check {check.slug!r} is {check.freshness!r}, not fresh")
        if not check.passed:
            reasons.append(f"check {check.slug!r} did not pass")
        if check.candidate and candidate and check.candidate != candidate:
            reasons.append(f"check {check.slug!r} was proven against {check.candidate}, not {candidate}")
    return reasons


def _has_fresh_acceptance(checks: tuple[CheckState, ...]) -> bool:
    return any(
        check.acceptance and check.passed and check.freshness == "fresh" for check in checks
    )


def _review_reasons(
    policy: Charter, review: ReviewState | None, candidate: str | None
) -> list[str]:
    if review is None:
        return ["no review was recorded"]
    reasons: list[str] = []
    if review.verdict != APPROVED:
        reasons.append(f"the review verdict is {review.verdict!r}, not {APPROVED!r}")
    if not review.candidate:
        reasons.append("the review is not bound to a candidate commit")
    elif review.candidate != candidate:
        reasons.append(f"the review approved {review.candidate}, not {candidate}")
    if not review.reviewer:
        reasons.append("the review has no reviewer")
    if not review.model:
        reasons.append("the review names no reviewer model")
    if review.implementer and review.implementer_model:
        separation = review_separation(
            review.implementer,
            review.reviewer,
            review.implementer_model,
            review.model,
            policy.reviewer_different_model,
        )
        if separation is not None:
            reasons.append(separation)
    return reasons
