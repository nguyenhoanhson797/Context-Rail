"""Triage: exact fingerprints, one vote per run and the pinned threshold."""

from __future__ import annotations

import pytest

from context_rail.kernel.budgets import RunFacts
from context_rail.kernel.triage import (
    ClusterDecision,
    ReportFacts,
    TriageThresholds,
    cluster_reports,
)

THRESHOLDS = TriageThresholds(triage_reports=2, triage_weight=2)


def report(
    record_id: str = "r1",
    run_id: str = "run1",
    fingerprint: str = "fp",
    target_class: str = "item",
    nothing_to_report: bool = False,
) -> ReportFacts:
    return ReportFacts(
        record_id, run_id, target_class, "parser", fingerprint,
        nothing_to_report=nothing_to_report,
    )


def run(run_id: str = "run1", failed: bool = False) -> RunFacts:
    return RunFacts(None, "implementer", 1, 1.0, (), failed=failed, run_id=run_id)


def test_one_report_is_deferred():
    decision = cluster_reports((report(),), (run(),), THRESHOLDS)[0]
    assert decision.action == "deferred"
    assert decision.reports == ("r1",)
    assert decision.runs == ("run1",)


def test_two_reports_from_one_run_are_one_vote():
    reports = (report("r1", "run1"), report("r2", "run1"))
    decision = cluster_reports(reports, (run(),), THRESHOLDS)[0]
    assert decision.action == "deferred"
    assert decision.runs == ("run1",)
    assert decision.weight == 1


def test_two_distinct_clean_runs_act():
    reports = (report("r1", "run1"), report("r2", "run2"))
    decision = cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS)[0]
    assert decision.action == "fix"
    assert decision.weight == 2
    assert decision.reports == ("r1", "r2")
    assert decision.runs == ("run1", "run2")


def test_a_failed_run_weighs_two():
    thresholds = TriageThresholds(triage_reports=1, triage_weight=2)
    failed = cluster_reports((report(),), (run(failed=True),), thresholds)[0]
    assert failed.action == "fix" and failed.weight == 2
    clean = cluster_reports((report(),), (run(),), thresholds)[0]
    assert clean.action == "deferred" and clean.weight == 1


def test_a_report_from_an_unknown_run_weighs_one():
    thresholds = TriageThresholds(triage_reports=1, triage_weight=2)
    assert cluster_reports((report(run_id="missing"),), (), thresholds)[0].action == "deferred"


def test_nothing_to_report_is_acknowledged_without_a_fix():
    reports = (report("r1", "run1", nothing_to_report=True),)
    decision = cluster_reports(reports, (run(),), THRESHOLDS)[0]
    assert decision.action == "acknowledged"


def test_an_engine_cluster_is_exported():
    reports = (report("r1", "run1", target_class="engine"), report("r2", "run2", target_class="engine"))
    assert cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS)[0].action == "export"


def test_the_engine_flag_exports_a_cluster():
    reports = (report("r1", "run1"), report("r2", "run2"))
    decision = cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS, engine_cluster=True)[0]
    assert decision.action == "export"


@pytest.mark.parametrize("target_class", ["gate", "pack", "human-wait", "bogus"])
def test_a_human_owned_target_becomes_a_proposal(target_class):
    reports = (
        report("r1", "run1", target_class=target_class),
        report("r2", "run2", target_class=target_class),
    )
    assert cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS)[0].action == "proposal"


def test_grouping_is_the_exact_fingerprint():
    reports = (report("r1", "run1", fingerprint="fp"), report("r2", "run2", fingerprint="fp "))
    decisions = cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS)
    assert [(decision.fingerprint, decision.action) for decision in decisions] == [
        ("fp", "deferred"),
        ("fp ", "deferred"),
    ]


def test_a_blank_fingerprint_does_not_merge_reports():
    reports = (report("r1", "run1", fingerprint=""), report("r2", "run2", fingerprint=""))
    assert [decision.action for decision in cluster_reports(reports, (), THRESHOLDS)] == ["deferred", "deferred"]


def test_decisions_are_sorted_by_fingerprint():
    reports = (
        report("r3", "run3", fingerprint="b"),
        report("r1", "run1", fingerprint="a"),
        report("r2", "run2", fingerprint="a"),
    )
    decisions = cluster_reports(reports, (run("run1"), run("run2")), THRESHOLDS)
    assert [decision.fingerprint for decision in decisions] == ["a", "b"]
    assert decisions[0].action == "fix"


def test_both_thresholds_are_required():
    reports = (report("r1", "run1"), report("r2", "run2"))
    runs = (run("run1"), run("run2"))
    assert cluster_reports(reports, runs, TriageThresholds(3, 2))[0].action == "deferred"
    assert cluster_reports(reports, runs, TriageThresholds(2, 3))[0].action == "deferred"


def test_decisions_are_frozen_and_complete():
    decision = cluster_reports((report(),), (run(),), THRESHOLDS)[0]
    assert isinstance(decision, ClusterDecision)
    assert set(decision.__dataclass_fields__) == {
        "fingerprint", "reports", "runs", "weight", "action", "reason"
    }
    assert decision.reason


@pytest.mark.parametrize("thresholds", [TriageThresholds(0, 1), TriageThresholds(1, 0), TriageThresholds(-1, 2)])
def test_non_positive_thresholds_are_refused(thresholds):
    with pytest.raises(ValueError):
        cluster_reports((report(),), (run(),), thresholds)
