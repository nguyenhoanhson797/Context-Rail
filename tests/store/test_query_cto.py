"""Bounded CTO queries over the index projections."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from context_rail.store import query
from context_rail.store.query import QueryError
from helpers import write_candidate, write_record, write_review_verdict

LEDGER_KEYS = {
    "candidate",
    "record_id",
    "item",
    "commit",
    "attempt",
    "actor",
    "seat",
    "profile",
    "profile_version",
    "model",
    "harness",
    "duration_s",
    "usage",
    "verdict",
    "report",
    "at",
}


def test_check_states_binds_the_latest_verdict(seeded_store):
    rows = query.check_states(seeded_store.ix, "login-page")
    assert [row["slug"] for row in rows] == ["unit"]
    row = rows[0]
    assert row["item"] == "login-page"
    assert row["mode"] == "cmd"
    assert row["acceptance"] is True
    assert row["definition_etag"] == seeded_store.check_etag
    assert row["passed"] is True
    assert row["verdict"] == seeded_store.verdict
    assert row["check_etag"] == seeded_store.check_etag
    assert row["input_blobs"] == {"src/login.py": "oid123"}
    assert row["input_globs"] == ["src/**/login*.py"]
    assert row["candidate_commit"] == seeded_store.candidate
    assert row["runner_digest"] == "sha256:verdict"
    assert row["spec"]["cmd"] == ["py", "-m", "pytest", "-q"]


def test_check_states_are_empty_for_an_item_without_checks(seeded_store):
    assert query.check_states(seeded_store.ix, "t1") == []


def test_ledger_row_shape_and_filters(seeded_store):
    value = query.ledger(seeded_store.ix, item="login-page")
    assert value["total"] == 1
    assert value["omitted"] == 0
    row = value["candidates"][0]
    assert set(row) == LEDGER_KEYS
    assert row["candidate"] == seeded_store.candidate_id
    assert row["record_id"] == seeded_store.candidate_id
    assert row["commit"] == seeded_store.candidate
    assert row["profile"] == "implementer"
    assert row["attempt"] == 2
    assert row["duration_s"] == 32.0
    assert row["verdict"] == "approve"
    assert row["report"] == seeded_store.report
    assert query.ledger(seeded_store.ix, profile="reviewer")["candidates"] == []


def test_ledger_never_counts_unknown_as_zero(seeded_store):
    write_candidate(
        seeded_store.store,
        "01JCANDIDATE00000000000002",
        seeded_store.item,
        "d" * 40,
        usage={"output_tokens": 1, "factory_credits": "unknown"},
        at="2026-10-28T11:00:00Z",
    )
    seeded_store.ix.sync()
    totals = query.ledger(seeded_store.ix, item="login-page")["totals"]
    assert totals["known_sum"]["output_tokens"] == 5
    assert totals["known_sum"]["input_tokens"] == 2
    assert totals["unknown_attempts"]["factory_credits"] == 2
    assert "factory_credits" not in {k for k, v in totals["known_sum"].items() if v}


def test_ledger_since_filters_by_candidate_time(seeded_store):
    value = query.ledger(seeded_store.ix, since="2026-10-28T10:00:32Z")
    assert value["total"] == 1
    later = query.ledger(seeded_store.ix, since="2026-10-28T10:00:33Z")
    assert later["candidates"] == [] and later["total"] == 0
    assert later["totals"] == {"known_sum": {}, "unknown_attempts": {}}


def test_ledger_bounds_rows_and_reports_omissions(seeded_store):
    write_candidate(
        seeded_store.store,
        "01JCANDIDATE00000000000002",
        seeded_store.item,
        "d" * 40,
        usage={"output_tokens": 1},
        at="2026-10-28T11:00:10Z",
    )
    seeded_store.ix.sync()
    value = query.ledger(seeded_store.ix, item=seeded_store.item, limit=1)
    assert value["total"] == 2
    assert len(value["candidates"]) == 1
    assert value["omitted"] == 1
    assert value["candidates"][0]["record_id"] == "01JCANDIDATE00000000000002"


@pytest.mark.parametrize("limit", [0, 101])
def test_ledger_refuses_out_of_range_limits(seeded_store, limit):
    with pytest.raises(QueryError):
        query.ledger(seeded_store.ix, limit=limit)


def test_untriaged_reports_lists_and_bounds(seeded_store):
    ix = seeded_store.ix
    reports = query.untriaged_reports(ix)
    assert [row["report"] for row in reports] == [seeded_store.report]
    row = reports[0]
    assert row["fingerprint"] == "engine|parser|every token is kept|one token is lost"
    assert row["nothing"] is False
    assert row["candidate"] == seeded_store.candidate_id
    assert len(query.untriaged_reports(ix, limit=1)) == 1
    with pytest.raises(QueryError):
        query.untriaged_reports(ix, limit=0)


def test_human_wait_reports_count_beyond_the_page(seeded_store):
    """Waits are queried on their own bound, so the total is never page-derived."""
    ix = seeded_store.ix
    assert query.human_wait_reports(ix, 1) == ([], 0)

    def wait(record_id: str, at: str) -> None:
        write_record(
            seeded_store.store,
            record_id,
            "report.filed",
            {
                "schema": 1,
                "target_class": "human-wait",
                "target": "payer copy",
                "item": seeded_store.item,
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
            about=(seeded_store.item,),
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


def test_human_wait_reports_use_their_own_index(seeded_store):
    """The wait read stays a seek at ten thousand reports, never a table scan."""
    plan = [
        row[3]
        for row in seeded_store.ix.conn.execute(
            "EXPLAIN QUERY PLAN SELECT p.record_id FROM reports p "
            "WHERE p.target_class = 'human-wait' AND NOT EXISTS "
            "(SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id) "
            "ORDER BY p.at DESC, p.record_id DESC LIMIT 20"
        )
    ]
    assert any("reports_class_at" in step for step in plan), plan


def test_reversible_decisions_and_rejected_reviews_report_their_totals(
    seeded_store,
):
    ix = seeded_store.ix
    decisions, decision_total = query.reversible_decisions(ix, limit=1)
    rejected, rejected_total = query.rejected_reviews(ix, limit=1)
    assert decisions == [] and decision_total == 0
    assert rejected == [] and rejected_total == 0

    write_record(
        seeded_store.store,
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
    write_review_verdict(
        seeded_store.store,
        "01JREJECTIONSTORE000000001",
        seeded_store.item,
        seeded_store.candidate,
        verdict="reject",
        at="2026-10-28T10:46:00Z",
    )
    ix.sync()
    decisions, decision_total = query.reversible_decisions(ix, limit=1)
    rejected, rejected_total = query.rejected_reviews(ix, limit=1)
    assert [row["class"] for row in decisions] == ["cto-reversible"]
    assert decision_total == 1
    assert [row["review"] for row in rejected] == ["01JREJECTIONSTORE000000001"]
    assert rejected_total == 1


def test_review_for_requires_the_exact_candidate(seeded_store):
    ix = seeded_store.ix
    review = query.review_for(ix, seeded_store.candidate)
    assert review is not None
    assert review["review"] == seeded_store.review
    assert review["verdict"] == "approve"
    assert review["reviewer"] == "reviewer"
    assert review["run"] is None
    assert query.review_for(ix, "c" * 40) is None


def test_approval_for_returns_the_latest_target(seeded_store):
    ix = seeded_store.ix
    latest = query.approval_for(ix, "charter")
    assert latest is not None
    assert latest["approval"] == seeded_store.approval
    assert latest["confirmer"] == "validator"
    assert latest["confirmation"] == "APPROVE charter"
    assert latest["protected_hashes"]["rail.yaml"] == "a" * 64

    write_record(
        seeded_store.store,
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


def test_brief_rows_reports_complete_counts_with_bounded_feeds(seeded_store):
    ix = seeded_store.ix
    value = query.brief_rows(ix, limit=1)
    assert value["counts"]["candidates"] == 1
    assert value["counts"]["checks"] == 1
    assert value["counts"]["reports"] == {"total": 1, "untriaged": 1}
    assert value["counts"]["items"]["total"] > 0
    assert value["counts"]["items"]["by_category"]["active"] >= 1
    assert len(value["items"]) == 1
    assert len(value["reports"]) == 1
    assert len(value["candidates"]) == 1
    assert len(value["approvals"]) == 1
    assert value["approvals"][0]["target"] == "charter"


def test_digest_rows_summarizes_quality_and_usage(seeded_store):
    ix = seeded_store.ix
    value = query.digest_rows(ix)
    assert value["counts"]["candidates"] == 1
    assert value["counts"]["reports"] == 1
    assert value["counts"]["untriaged_reports"] == 1
    assert value["counts"]["verdicts"] == 1
    assert value["counts"]["reviews"] == 1
    assert value["quality"]["verdicts"] == {"passed": 1, "failed": 0}
    assert value["quality"]["reviews"] == {"approve": 1, "reject": 0}
    assert value["quality"]["candidates"] == {"accepted": 1, "rejected": 0, "pending": 0}
    assert value["usage"]["known_sum"]["output_tokens"] == 4
    assert len(value["candidates"]) == 1
    assert len(value["reports"]) == 1


def test_digest_rows_since_excludes_older_activity(seeded_store):
    value = query.digest_rows(seeded_store.ix, since="2026-10-28T10:11:30Z")
    assert value["counts"]["candidates"] == 0
    assert value["counts"]["reports"] == 0
    assert value["counts"]["verdicts"] == 0
    assert value["usage"] == {"known_sum": {}, "unknown_attempts": {}}


def test_cto_queries_never_run_processes_or_read_store_files(
    seeded_store, monkeypatch
):
    def refuse(*args, **kwargs):
        raise AssertionError("a CTO read must not execute or open a store file")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    ix = seeded_store.ix
    assert query.check_states(ix, seeded_store.item)
    query.ledger(ix)
    query.untriaged_reports(ix)
    query.review_for(ix, seeded_store.candidate)
    query.approval_for(ix, "charter")
    query.brief_rows(ix)
    query.digest_rows(ix)
