"""Triage clusters reports into deferred, acknowledged, fix, proposal or export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import latest_record, records_of, write_record, write_run

TRIAGE_VALUE_KEYS = {"triaged", "deferred", "fix_items", "proposals", "engine_issues"}


def _triage(store: Path, key: str | None = None) -> dict:
    args = {} if key is None else {"key": key}
    return invoke("triage", args, store=store, actor="cto")


def _file_report(store: Path, item: str, run: str, path: Path, **overrides: Any) -> str:
    payload = {
        "schema": 1,
        "target_class": "engine",
        "target": "parser",
        "nothing_to_report": False,
        "what_happened": "the parser dropped a token",
        "expected": "every token is kept",
        "observed": "one token is lost",
        "impact": "medium",
        "evidence": ["01JVERDICT000000000000001"],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    result = invoke(
        "report",
        {"item": item, "run": run, "report_file": str(path)},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result
    return result["value"]["report"]


def _items(store: Path) -> list[Path]:
    return sorted((store / "items").rglob("*.md"))


def test_single_weight_one_report_is_deferred(
    triage_store: SimpleNamespace,
) -> None:
    items_before = _items(triage_store.store)
    result = _triage(triage_store.store)
    assert is_ok(result), result
    assert result["value"]["deferred"] == [triage_store.report]
    assert result["value"]["fix_items"] == []
    assert result["value"]["proposals"] == []
    assert records_of(triage_store.store, "report.triaged") == []
    assert _items(triage_store.store) == items_before


def test_two_distinct_failing_runs_create_a_linked_fix(
    triage_store_two_reports: SimpleNamespace,
) -> None:
    store = triage_store_two_reports.store
    result = _triage(store, "k-t4")
    assert is_ok(result), result
    fix = result["value"]["fix_items"][0]

    shown = invoke("show", {"slug": fix}, store=store, actor="cto")["value"]
    assert set(shown["out"]["because_of"]) == set(
        triage_store_two_reports.report_ids
    )
    assert set(shown["out"]["resolves"]) == set(
        triage_store_two_reports.report_ids
    )

    record = latest_record(store, "report.triaged")
    data = record["data"]
    assert set(data) == {"schema", "reports", "cluster", "weight", "action", "outputs"}
    assert sorted(data["reports"]) == sorted(triage_store_two_reports.report_ids)
    assert data["cluster"] == triage_store_two_reports.fingerprint
    assert data["weight"] >= 2
    assert data["action"] == "fix"
    assert data["outputs"] == [fix]
    assert (store / "items" / "fix" / f"{fix}.md").is_file()
    assert sorted(result["value"]["triaged"]) == sorted(
        triage_store_two_reports.report_ids
    )


def test_a_failed_run_doubles_the_weight(
    triage_store_two_reports: SimpleNamespace,
) -> None:
    store = triage_store_two_reports.store
    first = _triage(store, "k-weight")
    assert is_ok(first), first
    record = latest_record(store, "report.triaged")
    assert record["data"]["weight"] == 3


def test_human_only_target_becomes_a_proposal(
    triage_store_human_target: SimpleNamespace,
) -> None:
    store = triage_store_human_target.store
    charter = store / "charter.md"
    before = hashlib.sha256(charter.read_bytes()).hexdigest()
    approval_before = invoke("show", {"slug": "charter"}, store=store, actor="human")[
        "value"
    ]["approval"]["state"]
    items_before = _items(store)

    result = _triage(store, "k-t5")
    assert is_ok(result), result
    assert result["value"]["fix_items"] == []
    proposal = result["value"]["proposals"][0]

    shown = invoke("show", {"slug": proposal}, store=store, actor="cto")["value"]
    fields = shown["doc"]["fields"]
    assert fields["decision_class"] == "human-only"
    assert fields["proposed_change"]
    assert shown["doc"]["primitive"] == "note"

    assert hashlib.sha256(charter.read_bytes()).hexdigest() == before
    approval_after = invoke("show", {"slug": "charter"}, store=store, actor="human")
    assert approval_after["value"]["approval"]["state"] == approval_before == "approved"
    assert items_before == _items(store)


def test_engine_cluster_exports_inside_the_store(
    triage_store_engine: SimpleNamespace,
) -> None:
    store = triage_store_engine.store
    outside = {
        path
        for path in store.parent.rglob("*")
        if path.is_file() and not path.is_relative_to(store)
    }
    result = _triage(store, "k-t6")
    assert is_ok(result), result
    exported = result["value"]["engine_issues"][0]
    assert exported.startswith("engine-issues/")
    assert exported.endswith(".md")
    target = store / exported
    assert target.is_file()

    text = target.read_text(encoding="utf-8")
    evidence = [
        line
        for line in text.splitlines()
        if line.startswith("- ") and "evidence-" in line
    ]
    assert 0 < len(evidence) <= 20
    assert triage_store_engine.fingerprint in text

    leaked = {
        path
        for path in store.parent.rglob("*")
        if path.is_file() and not path.is_relative_to(store)
    }
    assert leaked == outside

    record = latest_record(store, "report.triaged")
    assert record["data"]["action"] == "export"
    assert record["data"]["outputs"] == [exported]


def test_an_engine_export_is_an_artifact_not_a_document(
    triage_store_engine: SimpleNamespace,
) -> None:
    store = triage_store_engine.store
    result = _triage(store, "k-t6b")
    assert is_ok(result), result
    exported = result["value"]["engine_issues"][0]

    doctor = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(doctor), doctor
    offenders = [
        issue
        for issue in doctor["value"]["issues"]
        if issue["path"] == exported
    ]
    assert offenders == []

    found = invoke("find", {"text": "engine issue"}, store=store, actor="cto")
    assert is_ok(found), found
    assert exported not in [row["path"] for row in found["value"]["items"]]
    assert result["value"]["triaged"]


def test_nothing_to_report_is_acknowledged_without_a_fix(
    triage_store_nothing: SimpleNamespace,
) -> None:
    store = triage_store_nothing.store
    items_before = _items(store)
    result = _triage(store)
    assert is_ok(result), result
    assert result["value"]["triaged"] == [triage_store_nothing.report]
    assert result["value"]["fix_items"] == []
    assert records_of(store, "report.triaged")[0]["data"]["action"] == "acknowledged"
    assert _items(store) == items_before


def test_triage_is_idempotent(triage_store_two_reports: SimpleNamespace) -> None:
    store = triage_store_two_reports.store
    first = _triage(store, "k-first")
    assert is_ok(first), first
    items_after_first = _items(store)

    again = _triage(store, "k-t7")
    assert is_ok(again), again
    assert again["value"]["triaged"] == []
    assert again["value"]["fix_items"] == []
    assert again["value"]["proposals"] == []
    assert again["value"]["engine_issues"] == []
    assert len(records_of(store, "report.triaged")) == 1
    assert _items(store) == items_after_first


def test_the_pinned_value_shape(triage_store_two_reports: SimpleNamespace) -> None:
    result = _triage(triage_store_two_reports.store, "k-shape")
    assert is_ok(result), result
    assert set(result["value"]) == TRIAGE_VALUE_KEYS


def test_an_engine_export_is_named_after_its_cluster(
    store_factory: Any, tmp_path: Path
) -> None:
    store = store_factory(docs=2)
    runs = ("01JRUN000000000000000701", "01JRUN000000000000000702")
    for number, run in enumerate(runs):
        write_run(store, run, item="t0", at="2026-10-28T12:00:00Z")
        _file_report(
            store,
            "t0",
            run,
            tmp_path / f"engine-{number}.json",
            evidence=[f"evidence-{number}"],
        )

    result = _triage(store, "k-engine-named")
    assert is_ok(result), result
    record = latest_record(store, "report.triaged")
    cluster = record["data"]["cluster"]
    exported = result["value"]["engine_issues"][0]
    assert exported == f"engine-issues/{cluster}.md"
    assert (store / exported).is_file()
    assert len(set(record["data"]["reports"])) == 2


def test_reports_without_a_run_id_vote_separately(
    triage_store_anonymous: SimpleNamespace,
) -> None:
    result = _triage(triage_store_anonymous.store, "k-anonymous")
    assert is_ok(result), result
    assert len(result["value"]["fix_items"]) == 1
    record = latest_record(triage_store_anonymous.store, "report.triaged")
    assert sorted(record["data"]["reports"]) == sorted(
        triage_store_anonymous.report_ids
    )
    assert record["data"]["weight"] == 2


def test_an_engine_export_bounds_its_report_lines(store_factory: Any) -> None:
    store = store_factory(docs=1)
    fingerprint = "engine|parser|every token is kept|one token is lost"
    for number in range(21):
        write_record(
            store,
            f"01JREPORT00000000000{number:04d}",
            "report.filed",
            {
                "schema": 1,
                "target_class": "engine",
                "target": "parser",
                "item": "t0",
                "run": None,
                "nothing_to_report": False,
                "what_happened": f"the parser dropped a token ({number})",
                "expected": "every token is kept",
                "observed": "one token is lost",
                "impact": "medium",
                "evidence": [],
                "fingerprint": fingerprint,
            },
            at="2026-10-28T12:00:00Z",
        )

    result = _triage(store, "k-bounded")
    assert is_ok(result), result
    exported = result["value"]["engine_issues"][0]
    text = (store / exported).read_text(encoding="utf-8")
    reports_section = text.split("## Evidence")[0].split("## Reports")[1]
    bullets = [
        line for line in reports_section.splitlines() if line.startswith("- 01JREPORT")
    ]
    assert len(bullets) == 20
    assert "1 more report(s) omitted" in text


def test_a_rejected_run_doubles_the_weight(store_factory: Any, tmp_path: Path) -> None:
    store = store_factory(docs=2)
    write_run(store, "01JRUN000000000000000801", item="t0")
    write_run(
        store, "01JRUN000000000000000802", item="t0", accepted=False
    )
    for number, run in enumerate(
        ("01JRUN000000000000000801", "01JRUN000000000000000802")
    ):
        _file_report(
            store,
            "t0",
            run,
            tmp_path / f"reject-{number}.json",
            target_class="item",
            target="t0",
        )

    result = _triage(store, "k-rejected")
    assert is_ok(result), result
    record = latest_record(store, "report.triaged")
    assert record["data"]["weight"] == 3
    assert len(result["value"]["fix_items"]) == 1


def test_an_unsafe_cluster_name_still_exports(
    store_factory: Any,
) -> None:
    store = store_factory(docs=1)
    for number, record_id in enumerate(
        ("01JREPORT0000000000000801", "01JREPORT0000000000000802")
    ):
        write_record(
            store,
            record_id,
            "report.filed",
            {
                "schema": 1,
                "target_class": "engine",
                "target": "parser",
                "item": "t0",
                "run": None,
                "nothing_to_report": False,
                "what_happened": f"the parser dropped a token ({number})",
                "expected": "every token is kept",
                "observed": "one token is lost",
                "impact": "medium",
                "evidence": [],
                "fingerprint": "con",
            },
            at="2026-10-28T12:00:00Z",
        )

    result = _triage(store, "k-reserved")
    assert is_ok(result), result
    exported = result["value"]["engine_issues"][0]
    assert exported.startswith("engine-issues/")
    assert (store / exported).is_file()
    assert Path(exported).stem.upper() not in {"CON", "PRN", "AUX", "NUL"}
