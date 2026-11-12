"""Run records written by the retired launcher stay on disk and are ignored."""

from __future__ import annotations

from context_rail.core.result import is_ok
from context_rail.doctor.checks import SEVERITIES
from context_rail.gateway.gateway import invoke
from helpers import write_record


def _legacy_run(store, record_id: str = "01JLEGACYRUN00000000000001") -> None:
    write_record(
        store,
        record_id,
        "run.completed",
        {
            "schema": 1,
            "dispatch_id": "dispatch-1",
            "role": "implementer",
            "item": "t0",
            "status": "verified",
            "attempts": [],
            "usage": {"input_tokens": 3},
        },
        at="2026-10-28T12:00:00Z",
        about=("t0",),
    )


def test_a_legacy_run_record_is_not_indexed(store_factory) -> None:
    store = store_factory(docs=3)
    _legacy_run(store)

    rebuilt = invoke("reindex", {"full": True}, store=store, actor="cto")
    assert is_ok(rebuilt), rebuilt

    ledger = invoke("ledger", {}, store=store, actor="cto")
    assert is_ok(ledger), ledger
    assert ledger["value"]["candidates"] == []
    assert ledger["value"]["total"] == 0
    assert ledger["value"]["totals"] == {"known_sum": {}, "unknown_attempts": {}}


def test_a_legacy_run_record_raises_no_doctor_issue(store_factory) -> None:
    store = store_factory(docs=3)
    _legacy_run(store)

    result = invoke("doctor", {}, store=store, actor="human")

    assert is_ok(result), result
    codes = {issue["code"] for issue in result["value"]["issues"]}
    assert not {"report_missing", "run_incomplete"} & codes


def test_the_doctor_no_longer_names_run_codes() -> None:
    assert "report_missing" not in SEVERITIES
    assert "run_incomplete" not in SEVERITIES
