"""Triage: which report clusters have earned a fix, a proposal or an export.

Grouping is the exact fingerprint. One vote per distinct run, doubled when that
run recorded a failure or a rejected Check. Nothing to report is acknowledged
without a fix, engine clusters are exported for engine work, and a cluster the
CTO does not own becomes a proposal for a human.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from context_rail.kernel.budgets import RunFacts

__all__ = [
    "ACTIONS",
    "CTO_OWNED_CLASSES",
    "ENGINE_CLASS",
    "ClusterDecision",
    "ReportFacts",
    "TriageThresholds",
    "cluster_reports",
    "report_fingerprint",
]

ACTIONS = ("deferred", "acknowledged", "fix", "proposal", "export")
ENGINE_CLASS = "engine"
CTO_OWNED_CLASSES = ("item", "skill", "profile", "tool", "environment")
UNKNOWN_RUN = "unknown-run"


@dataclass(frozen=True)
class ReportFacts:
    """One filed report, as the index projects it."""

    record_id: str
    run_id: str
    target_class: str
    target: str
    fingerprint: str
    nothing_to_report: bool = False


@dataclass(frozen=True)
class TriageThresholds:
    """How many distinct reports, at what weight, earn an action."""

    triage_reports: int
    triage_weight: int


@dataclass(frozen=True)
class ClusterDecision:
    """One cluster's vote and what triage may do with it."""

    fingerprint: str
    reports: tuple[str, ...]
    runs: tuple[str, ...]
    weight: int
    action: str
    reason: str


def report_fingerprint(
    target_class: str, target: str, expected: str, observed: str
) -> str:
    """The canonical grouping identity of a report: its exact normalized text.

    Membership is decided by this one string alone, so two reports that look
    alike to a reader but differ in a word stay in different clusters.
    """
    parts = (
        _normalized(target_class),
        _normalized(target),
        _normalized(expected),
        _normalized(observed),
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(str(text).split())


def cluster_reports(
    reports: Sequence[ReportFacts],
    runs: Sequence[RunFacts],
    thresholds: TriageThresholds,
    *,
    engine_cluster: bool = False,
) -> tuple[ClusterDecision, ...]:
    """Group reports by exact fingerprint and decide every cluster."""
    if thresholds.triage_reports < 1 or thresholds.triage_weight < 1:
        raise ValueError("triage thresholds must be positive")
    failed = {run.run_id for run in runs if run.run_id and run.failed}
    groups: dict[str, list[ReportFacts]] = {}
    for report in reports:
        key = report.fingerprint or f"report:{report.record_id}"
        groups.setdefault(key, []).append(report)
    decisions = [
        _decide(key, members, failed, thresholds, engine_cluster)
        for key, members in sorted(groups.items())
    ]
    return tuple(decisions)


def _decide(
    fingerprint: str,
    members: list[ReportFacts],
    failed: set[str | None],
    thresholds: TriageThresholds,
    engine_cluster: bool,
) -> ClusterDecision:
    voting = [member for member in members if not member.nothing_to_report]
    distinct_runs = tuple(sorted({member.run_id or UNKNOWN_RUN for member in voting}))
    weight = sum(2 if run_id in failed else 1 for run_id in distinct_runs)
    reports = tuple(sorted(member.record_id for member in members))
    action, reason = _action(fingerprint, members, distinct_runs, weight, thresholds, engine_cluster)
    return ClusterDecision(fingerprint, reports, distinct_runs, weight, action, reason)


def _action(
    fingerprint: str,
    members: list[ReportFacts],
    distinct_runs: tuple[str, ...],
    weight: int,
    thresholds: TriageThresholds,
    engine_cluster: bool,
) -> tuple[str, str]:
    if all(member.nothing_to_report for member in members):
        return "acknowledged", f"cluster {fingerprint!r} has nothing to report"
    enough = (
        len(distinct_runs) >= thresholds.triage_reports
        and weight >= thresholds.triage_weight
    )
    if not enough:
        return "deferred", (
            f"cluster {fingerprint!r} has {len(distinct_runs)} distinct run(s) weighing {weight};"
            f" the threshold is {thresholds.triage_reports} reports weighing {thresholds.triage_weight}"
        )
    classes = {member.target_class for member in members}
    if engine_cluster or ENGINE_CLASS in classes:
        return "export", f"cluster {fingerprint!r} targets the engine"
    if not classes <= set(CTO_OWNED_CLASSES):
        owned = ", ".join(sorted(classes))
        return "proposal", f"cluster {fingerprint!r} targets human-owned classes: {owned}"
    return "fix", (
        f"cluster {fingerprint!r} has {len(distinct_runs)} distinct run(s) weighing {weight}"
    )
