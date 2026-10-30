"""Triage attribution through a run's report link, bounded exports and key replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import latest_record, records_of, write_record, write_run

STAMP = "2026-10-28T12:00:00Z"
FINGERPRINT = "item|t0|the button submits|the form reloaded"
ENGINE_FINGERPRINT = "engine|parser|every token is kept|one token is lost"


def _triage(store: Path, key: str | None = None) -> dict:
    args: dict[str, Any] = {} if key is None else {"key": key}
    return invoke("triage", args, store=store, actor="cto")


def _seed_report(
    store: Path,
    record_id: str,
    *,
    run: str | None = None,
    fingerprint: str = FINGERPRINT,
    target_class: str = "item",
    target: str = "t0",
    evidence: tuple[str, ...] = (),
    at: str = STAMP,
) -> str:
    write_record(
        store,
        record_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": target_class,
            "target": target,
            "item": "t0",
            "run": run,
            "nothing_to_report": False,
            "what_happened": "the button did nothing",
            "expected": "the button submits",
            "observed": "the form reloaded",
            "impact": "medium",
            "evidence": list(evidence),
            "fingerprint": fingerprint,
        },
        at=at,
    )
    return record_id


def _doctor_codes(store: Path) -> list[str]:
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    return [issue["code"] for issue in result["value"]["issues"]]


# ── attribution through the run's report link ─────────────────────────────


def test_dispatch_shaped_reports_weigh_two_for_a_failed_run(store_factory: Any) -> None:
    store = store_factory(docs=3)
    good_report = _seed_report(store, "01JREPORT0000000000000901", run=None, at=STAMP)
    bad_report = _seed_report(store, "01JREPORT0000000000000902", run=None, at=STAMP)
    write_run(
        store,
        "01JRUN000000000000000901",
        item="t0",
        status="verified",
        report=good_report,
        at=STAMP,
    )
    write_run(
        store,
        "01JRUN000000000000000902",
        item="t0",
        status="failed",
        report=bad_report,
        at=STAMP,
    )

    result = _triage(store, "k-dispatch-shape")

    assert is_ok(result), result
    record = latest_record(store, "report.triaged")
    assert sorted(record["data"]["reports"]) == sorted([good_report, bad_report])
    assert record["data"]["weight"] == 3


def test_two_reports_on_one_linked_run_count_once(store_factory: Any) -> None:
    store = store_factory(docs=3)
    run = "01JRUN000000000000000903"
    linked = _seed_report(store, "01JREPORT0000000000000903", run=None, at=STAMP)
    named = _seed_report(store, "01JREPORT0000000000000904", run=run, at=STAMP)
    write_run(store, run, item="t0", status="failed", report=linked, at=STAMP)

    result = _triage(store, "k-dedupe")

    assert is_ok(result), result
    assert sorted(result["value"]["deferred"]) == sorted([linked, named])
    assert records_of(store, "report.triaged") == []


def test_a_second_report_for_a_dispatch_linked_run_is_refused(
    store_factory: Any, tmp_path: Path
) -> None:
    store = store_factory(docs=2)
    run = "01JRUN000000000000000905"
    report_id = _seed_report(store, "01JREPORT0000000000000905", run=None, at=STAMP)
    write_run(store, run, item="t0", status="verified", report=report_id, at=STAMP)
    report_file = tmp_path / "second.json"
    report_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "target_class": "item",
                "target": "t0",
                "nothing_to_report": False,
                "what_happened": "the form reloaded again",
                "expected": "the button submits",
                "observed": "the form reloaded",
                "impact": "medium",
                "evidence": [],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = invoke(
        "report",
        {"item": "t0", "run": run, "report_file": str(report_file)},
        store=store,
        actor="cto",
    )

    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert len(records_of(store, "report.filed")) == 1


# ── the bounded engine-issues export ──────────────────────────────────────


def test_an_engine_export_is_rewritten_not_appended_across_recurrences(
    store_factory: Any,
) -> None:
    store = store_factory(docs=3)
    for number, run in enumerate(("01JRUN000000000000000911", "01JRUN000000000000000912")):
        write_run(store, run, item="t0", status="verified", at=STAMP)
        _seed_report(
            store,
            f"01JREPORT00000000000009{number + 11}",
            run=run,
            fingerprint=ENGINE_FINGERPRINT,
            target_class="engine",
            target="parser",
            evidence=tuple(f"evidence-first-{number}-{step}" for step in range(15)),
            at=STAMP,
        )

    first = _triage(store, "k-export-1")
    assert is_ok(first), first
    exported = first["value"]["engine_issues"][0]
    path = store / exported
    assert path.is_file()

    for number, run in enumerate(("01JRUN000000000000000913", "01JRUN000000000000000914")):
        write_run(store, run, item="t0", status="verified", at=STAMP)
        _seed_report(
            store,
            f"01JREPORT00000000000009{number + 13}",
            run=run,
            fingerprint=ENGINE_FINGERPRINT,
            target_class="engine",
            target="parser",
            evidence=tuple(f"evidence-second-{number}-{step}" for step in range(15)),
            at="2026-10-28T13:00:00Z",
        )

    second = _triage(store, "k-export-2")
    assert is_ok(second), second
    assert second["value"]["engine_issues"] == [exported]

    text = path.read_text(encoding="utf-8")
    assert text.count("# Engine issue:") == 1, text
    evidence = [
        line
        for line in text.splitlines()
        if line.startswith("- ") and "evidence-" in line
    ]
    assert 0 < len(evidence) <= 20, len(evidence)


# ── a reused key never reports an unwritten output ────────────────────────


def test_a_reused_triage_key_after_new_reports_does_the_new_work(
    store_factory: Any,
) -> None:
    store = store_factory(docs=3)
    for number, run in enumerate(("01JRUN000000000000000921", "01JRUN000000000000000922")):
        write_run(store, run, item="t0", status="verified", at=STAMP)
        _seed_report(
            store,
            f"01JREPORT00000000000009{number + 21}",
            run=run,
            at=STAMP,
        )

    first = _triage(store, "same")
    assert is_ok(first), first
    assert len(first["value"]["fix_items"]) == 1
    fixes_before = sorted((store / "items" / "fix").glob("*.md"))
    assert len(fixes_before) == 1

    for number, run in enumerate(("01JRUN000000000000000923", "01JRUN000000000000000924")):
        write_run(store, run, item="t0", status="verified", at=STAMP)
        _seed_report(
            store,
            f"01JREPORT00000000000009{number + 23}",
            run=run,
            at="2026-10-28T13:00:00Z",
        )

    second = _triage(store, "same")

    assert is_ok(second), second
    assert second["value"]["fix_items"], second
    for slug in second["value"]["fix_items"]:
        assert (store / "items" / "fix" / f"{slug}.md").is_file(), slug
    assert len(records_of(store, "report.triaged")) == 2
    assert "report_untriaged" not in _doctor_codes(store)
