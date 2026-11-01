"""Bounded CTO queries over the index projections."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from context_rail.store import query
from context_rail.store.query import QueryError
from helpers import write_record

LEDGER_KEYS = {
    "run",
    "record_id",
    "dispatch_id",
    "role",
    "item",
    "profile",
    "model",
    "start",
    "end",
    "duration_s",
    "status",
    "attempt_count",
    "candidate_commit",
    "accepted",
    "usage",
    "report",
}


def test_check_states_binds_the_latest_verdict(seeded_runs_store):
    rows = query.check_states(seeded_runs_store.ix, "login-page")
    assert [row["slug"] for row in rows] == ["unit"]
    row = rows[0]
    assert row["item"] == "login-page"
    assert row["mode"] == "cmd"
    assert row["acceptance"] is True
    assert row["definition_etag"] == seeded_runs_store.check_etag
    assert row["passed"] is True
    assert row["verdict"] == seeded_runs_store.verdict
    assert row["check_etag"] == seeded_runs_store.check_etag
    assert row["input_blobs"] == {"src/login.py": "oid123"}
    assert row["input_globs"] == ["src/**/login*.py"]
    assert row["candidate_commit"] == seeded_runs_store.candidate
    assert row["runner_digest"] == "sha256:verdict"
    assert row["spec"]["cmd"] == ["py", "-m", "pytest", "-q"]


def test_check_states_are_empty_for_an_item_without_checks(seeded_runs_store):
    assert query.check_states(seeded_runs_store.ix, "t1") == []


def test_ledger_row_shape_and_filters(seeded_runs_store):
    value = query.ledger(seeded_runs_store.ix, item="login-page")
    assert value["total"] == 1
    assert value["omitted"] == 0
    row = value["runs"][0]
    assert set(row) == LEDGER_KEYS
    assert row["run"] == seeded_runs_store.run
    assert row["record_id"] == seeded_runs_store.run
    assert row["dispatch_id"] == seeded_runs_store.dispatch
    assert row["role"] == "implementer"
    assert row["profile"] == "implementer"
    assert row["attempt_count"] == 2
    assert row["duration_s"] == 32.0
    assert row["report"] == seeded_runs_store.report
    assert query.ledger(seeded_runs_store.ix, profile="reviewer")["runs"] == []


def test_ledger_never_counts_unknown_as_zero(seeded_runs_store):
    totals = query.ledger(seeded_runs_store.ix, item="login-page")["totals"]
    assert totals["known_sum"] == {"input_tokens": 2, "output_tokens": 4}
    assert totals["unknown_attempts"]["factory_credits"] == 2
    assert "factory_credits" not in totals["known_sum"]


def test_ledger_since_filters_by_run_end(seeded_runs_store):
    value = query.ledger(seeded_runs_store.ix, since="2026-10-28T10:00:32Z")
    assert value["total"] == 1
    later = query.ledger(seeded_runs_store.ix, since="2026-10-28T10:00:33Z")
    assert later["runs"] == [] and later["total"] == 0
    assert later["totals"] == {"known_sum": {}, "unknown_attempts": {}}


def test_ledger_bounds_rows_and_reports_omissions(seeded_runs_store):
    write_record(
        seeded_runs_store.store,
        "01JRUN000000000000000002",
        "run.completed",
        {
            "schema": 1,
            "dispatch_id": "dispatch-2",
            "role": "implementer",
            "actor": "cto",
            "profile": "implementer",
            "profile_version": 1,
            "model": "custom:Deepseek-V4-flash-0",
            "item": seeded_runs_store.item,
            "started_at": "2026-10-28T11:00:00Z",
            "ended_at": "2026-10-28T11:00:10Z",
            "duration_s": 10.0,
            "status": "completed",
            "attempts": [
                {
                    "number": 1,
                    "start": "2026-10-28T11:00:00Z",
                    "end": "2026-10-28T11:00:10Z",
                    "duration_s": 10.0,
                    "status": "completed",
                    "usage": {"output_tokens": 1},
                }
            ],
            "usage": {"output_tokens": 1},
        },
        at="2026-10-28T11:00:10Z",
        about=(seeded_runs_store.item,),
    )
    seeded_runs_store.ix.sync()
    value = query.ledger(seeded_runs_store.ix, item=seeded_runs_store.item, limit=1)
    assert value["total"] == 2
    assert len(value["runs"]) == 1
    assert value["omitted"] == 1
    assert value["runs"][0]["dispatch_id"] == "dispatch-2"


@pytest.mark.parametrize("limit", [0, 101])
def test_ledger_refuses_out_of_range_limits(seeded_runs_store, limit):
    with pytest.raises(QueryError):
        query.ledger(seeded_runs_store.ix, limit=limit)


def test_untriaged_reports_lists_and_bounds(seeded_runs_store):
    ix = seeded_runs_store.ix
    reports = query.untriaged_reports(ix)
    assert [row["report"] for row in reports] == [seeded_runs_store.report]
    row = reports[0]
    assert row["fingerprint"] == "engine|parser|every token is kept|one token is lost"
    assert row["nothing"] is False
    assert row["run"] == seeded_runs_store.run
    assert len(query.untriaged_reports(ix, limit=1)) == 1
    with pytest.raises(QueryError):
        query.untriaged_reports(ix, limit=0)


def test_human_wait_reports_count_beyond_the_page(seeded_runs_store):
    """Waits are queried on their own bound, so the total is never page-derived."""
    ix = seeded_runs_store.ix
    assert query.human_wait_reports(ix, 1) == ([], 0)

    def wait(record_id: str, at: str) -> None:
        write_record(
            seeded_runs_store.store,
            record_id,
            "report.filed",
            {
                "schema": 1,
                "target_class": "human-wait",
                "target": "payer copy",
                "item": seeded_runs_store.item,
                "run": None,
                "nothing_to_report": False,
                "what_happened": "an older wait still needs a human",
                "expected": "a human decides",
                "observed": "undecided",
                "impact": "medium",
                "evidence": [],
                "fingerprint": "human-wait|payer copy",
            },
            at=at,
            about=(seeded_runs_store.item,),
        )

    wait("01JWAITSTORE00000000000001", "2026-10-28T10:40:00Z")
    ix.sync()
    shown, total = query.human_wait_reports(ix, 1)
    assert [row["report"] for row in shown] == ["01JWAITSTORE00000000000001"]
    assert total == 1

    wait("01JWAITSTORE00000000000002", "2026-10-28T09:00:00Z")
    ix.sync()
    shown, total = query.human_wait_reports(ix, 1)
    assert len(shown) == 1
    assert total == 2
    with pytest.raises(QueryError):
        query.human_wait_reports(ix, 0)


def test_human_wait_reports_use_their_own_index(seeded_runs_store):
    """The wait read stays a seek at ten thousand reports, never a table scan."""
    plan = [
        row[3]
        for row in seeded_runs_store.ix.conn.execute(
            "EXPLAIN QUERY PLAN SELECT p.record_id FROM reports p "
            "WHERE p.target_class = 'human-wait' AND NOT EXISTS "
            "(SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id) "
            "ORDER BY p.at DESC, p.record_id DESC LIMIT 20"
        )
    ]
    assert any("reports_class_at" in step for step in plan), plan


def test_reversible_decisions_and_rejected_reviews_report_their_totals(
    seeded_runs_store,
):
    ix = seeded_runs_store.ix
    decisions, decision_total = query.reversible_decisions(ix, limit=1)
    rejected, rejected_total = query.rejected_reviews(ix, limit=1)
    assert decisions == [] and decision_total == 0
    assert rejected == [] and rejected_total == 0

    write_record(
        seeded_runs_store.store,
        "01JREVERSIBLESTORE00000001",
        "decision.made",
        {
            "schema": 1,
            "decision_class": "cto-reversible",
            "reason": "reuses the login parser",
            "before": {"depends_on": []},
            "after": {"depends_on": ["login-page"]},
        },
        at="2026-10-28T10:45:00Z",
        about=("sign-in",),
    )
    write_record(
        seeded_runs_store.store,
        "01JREJECTIONSTORE000000001",
        "review.verdict",
        {
            "schema": 1,
            "item": seeded_runs_store.item,
            "implementation_run": seeded_runs_store.run,
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": seeded_runs_store.candidate,
            "verdict": "reject",
            "findings": ["empty input not handled"],
            "evidence": [],
            "profile_version": 1,
        },
        at="2026-10-28T10:46:00Z",
        about=(seeded_runs_store.item,),
    )
    ix.sync()
    decisions, decision_total = query.reversible_decisions(ix, limit=1)
    rejected, rejected_total = query.rejected_reviews(ix, limit=1)
    assert [row["class"] for row in decisions] == ["cto-reversible"]
    assert decision_total == 1
    assert [row["review"] for row in rejected] == ["01JREJECTIONSTORE000000001"]
    assert rejected_total == 1


def test_review_for_requires_the_exact_candidate(seeded_runs_store):
    ix = seeded_runs_store.ix
    review = query.review_for(ix, seeded_runs_store.candidate)
    assert review is not None
    assert review["review"] == seeded_runs_store.review
    assert review["verdict"] == "approve"
    assert review["reviewer"] == "reviewer"
    assert review["run"] == seeded_runs_store.run
    assert query.review_for(ix, "c" * 40) is None


def test_approval_for_returns_the_latest_target(seeded_runs_store):
    ix = seeded_runs_store.ix
    latest = query.approval_for(ix, "charter")
    assert latest is not None
    assert latest["approval"] == seeded_runs_store.approval
    assert latest["confirmer"] == "validator"
    assert latest["confirmation"] == "APPROVE charter"
    assert latest["protected_hashes"]["rail.yaml"] == "a" * 64

    write_record(
        seeded_runs_store.store,
        "01JAPPROVAL00000000000002",
        "approval.recorded",
        {
            "schema": 1,
            "target": "charter",
            "protected_hashes": {"rail.yaml": "d" * 64},
            "confirmer": "second",
            "confirmation": "APPROVE charter",
        },
        at="2026-10-28T10:50:00Z",
        actor="human",
        about=("charter",),
    )
    ix.sync()
    assert query.approval_for(ix, "charter")["approval"] == "01JAPPROVAL00000000000002"
    assert query.approval_for(ix, "pack:basic") is None


def test_brief_rows_reports_complete_counts_with_bounded_feeds(seeded_runs_store):
    ix = seeded_runs_store.ix
    value = query.brief_rows(ix, limit=1)
    assert value["counts"]["runs"] == 1
    assert value["counts"]["checks"] == 1
    assert value["counts"]["reports"] == {"total": 1, "untriaged": 1}
    assert value["counts"]["items"]["total"] > 0
    assert value["counts"]["items"]["by_category"]["active"] >= 1
    assert len(value["items"]) == 1
    assert len(value["reports"]) == 1
    assert len(value["runs"]) == 1
    assert len(value["approvals"]) == 1
    assert value["approvals"][0]["target"] == "charter"


def test_digest_rows_summarizes_quality_and_usage(seeded_runs_store):
    ix = seeded_runs_store.ix
    value = query.digest_rows(ix)
    assert value["counts"]["runs"] == 1
    assert value["counts"]["reports"] == 1
    assert value["counts"]["untriaged_reports"] == 1
    assert value["counts"]["verdicts"] == 1
    assert value["counts"]["reviews"] == 1
    assert value["quality"]["verdicts"] == {"passed": 1, "failed": 0}
    assert value["quality"]["reviews"] == {"approve": 1, "reject": 0}
    assert value["quality"]["runs"] == {"accepted": 1, "rejected": 0, "pending": 0}
    assert value["usage"]["known_sum"]["output_tokens"] == 4
    assert len(value["runs"]) == 1
    assert len(value["reports"]) == 1


def test_digest_rows_since_excludes_older_activity(seeded_runs_store):
    value = query.digest_rows(seeded_runs_store.ix, since="2026-10-28T10:11:30Z")
    assert value["counts"]["runs"] == 0
    assert value["counts"]["reports"] == 0
    assert value["counts"]["verdicts"] == 0
    assert value["usage"] == {"known_sum": {}, "unknown_attempts": {}}


def test_cto_queries_never_run_processes_or_read_store_files(
    seeded_runs_store, monkeypatch
):
    def refuse(*args, **kwargs):
        raise AssertionError("a CTO read must not execute or open a store file")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    ix = seeded_runs_store.ix
    assert query.check_states(ix, seeded_runs_store.item)
    query.ledger(ix)
    query.untriaged_reports(ix)
    query.review_for(ix, seeded_runs_store.candidate)
    query.approval_for(ix, "charter")
    query.brief_rows(ix)
    query.digest_rows(ix)
