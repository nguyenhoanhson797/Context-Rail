"""Every completed run owes exactly one structured report Record."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.kernel.triage import report_fingerprint
from helpers import records_of, write_run

REPORT_KEYS = {
    "schema",
    "target_class",
    "target",
    "item",
    "run",
    "nothing_to_report",
    "what_happened",
    "expected",
    "observed",
    "impact",
    "evidence",
    "fingerprint",
}


def _report(
    store: Path, item: str, run: str, report_file: Path, key: str | None = None
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "item": item,
        "run": run,
        "report_file": str(report_file),
    }
    if key is not None:
        args["key"] = key
    return invoke("report", args, store=store, actor="cto")


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


def test_one_report_per_run(completed_run: SimpleNamespace) -> None:
    first = _report(
        completed_run.store, completed_run.item, completed_run.run, completed_run.report_file
    )
    assert is_ok(first), first
    assert first["value"]["report"]

    second = _report(
        completed_run.store, completed_run.item, completed_run.run, completed_run.report_file
    )
    assert second["error"]["code"] == "CONFLICT"
    assert len(records_of(completed_run.store, "report.filed")) == 1


def test_report_data_carries_exactly_the_pinned_keys(
    completed_run: SimpleNamespace,
) -> None:
    result = _report(
        completed_run.store, completed_run.item, completed_run.run, completed_run.report_file
    )
    assert is_ok(result), result
    assert set(result["value"]) == {"report", "nothing_to_report"}
    record = records_of(completed_run.store, "report.filed")[0]
    data = record["data"]
    assert set(data) == REPORT_KEYS
    assert data["schema"] == 1
    assert data["item"] == completed_run.item
    assert data["run"] == completed_run.run
    assert data["impact"] in {"low", "medium", "high", "critical"}
    assert data["fingerprint"] == report_fingerprint(
        data["target_class"], data["target"], data["expected"], data["observed"]
    )
    assert result["value"]["report"] == record["id"]
    assert result["value"]["nothing_to_report"] is False


def test_a_replayed_key_returns_the_same_report(completed_run: SimpleNamespace) -> None:
    first = _report(
        completed_run.store,
        completed_run.item,
        completed_run.run,
        completed_run.report_file,
        key="k-replay",
    )
    assert is_ok(first), first
    second = _report(
        completed_run.store,
        completed_run.item,
        completed_run.run,
        completed_run.report_file,
        key="k-replay",
    )
    assert is_ok(second), second
    assert second["value"] == first["value"]
    assert len(records_of(completed_run.store, "report.filed")) == 1


def test_a_report_file_may_carry_its_own_item(
    tmp_path: Path,
    completed_run: SimpleNamespace,
    report_payload: Callable[..., dict[str, Any]],
) -> None:
    report_file = _write(tmp_path / "own-item.json", report_payload(item="t0"))
    result = invoke(
        "report",
        {"run": completed_run.run, "report_file": str(report_file)},
        store=completed_run.store,
        actor="cto",
    )
    assert is_ok(result), result
    record = records_of(completed_run.store, "report.filed")[0]
    assert record["data"]["item"] == "t0"
    assert record["about"] == ["t0"]


def test_the_fingerprint_collapses_whitespace_but_not_words() -> None:
    text = ("a bad password is refused", "the form signed the user in")
    canonical = report_fingerprint("item", "t0", text[0], text[1])
    spaced = report_fingerprint("item ", "t0", f"  {text[0]}\n", f"{text[1]} ")
    assert spaced == canonical
    assert canonical != report_fingerprint("item", "t0", text[0], "the form signed the user out")
    assert canonical != report_fingerprint("item", "t1", text[0], text[1])


def test_nothing_to_report_is_still_one_record(
    tmp_path: Path, completed_run: SimpleNamespace
) -> None:
    report_file = _write(
        tmp_path / "nothing.json",
        {
            "schema": 1,
            "target_class": "item",
            "target": completed_run.item,
            "nothing_to_report": True,
            "what_happened": "",
            "expected": "",
            "observed": "",
            "impact": "low",
            "evidence": [],
        },
    )
    result = _report(
        completed_run.store, completed_run.item, completed_run.run, report_file
    )
    assert is_ok(result), result
    assert result["value"]["nothing_to_report"] is True
    assert result["value"]["report"]
    reports = records_of(completed_run.store, "report.filed")
    assert len(reports) == 1
    assert reports[0]["data"]["evidence"] == []


def test_malformed_report_is_refused_and_writes_nothing(
    tmp_path: Path, completed_run: SimpleNamespace
) -> None:
    report_file = _write(
        tmp_path / "bad.json",
        {
            "schema": 1,
            "target_class": "item",
            "target": completed_run.item,
            "nothing_to_report": False,
            "what_happened": "something happened",
            "expected": "one thing",
            "observed": "another",
            "impact": "huge",
            "evidence": [],
        },
    )
    result = _report(
        completed_run.store, completed_run.item, completed_run.run, report_file
    )
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(completed_run.store, "report.filed") == []


def test_an_unknown_report_key_is_refused(
    tmp_path: Path,
    completed_run: SimpleNamespace,
    report_payload: Callable[..., dict[str, Any]],
) -> None:
    payload = report_payload()
    payload["notes"] = "extra"
    report_file = _write(tmp_path / "extra.json", payload)
    result = _report(
        completed_run.store, completed_run.item, completed_run.run, report_file
    )
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(completed_run.store, "report.filed") == []


def test_doctor_flags_missing_and_untriaged_reports(
    completed_run: SimpleNamespace,
) -> None:
    codes = _doctor_codes(completed_run.store)
    assert "report_missing" in codes
    assert "report_untriaged" not in codes

    filed = _report(
        completed_run.store, completed_run.item, completed_run.run, completed_run.report_file
    )
    assert is_ok(filed), filed
    codes = _doctor_codes(completed_run.store)
    assert "report_missing" not in codes
    assert "report_untriaged" in codes


def test_doctor_counts_a_run_report_link_as_its_report(
    completed_run: SimpleNamespace,
) -> None:
    filed = _report(
        completed_run.store, completed_run.item, completed_run.run, completed_run.report_file
    )
    assert is_ok(filed), filed
    report_id = filed["value"]["report"]
    store = completed_run.store
    write_run(store, "01JRUN000000000000000102", item="t0", report=report_id)
    codes = [issue for issue in _doctor_codes(store) if issue == "report_missing"]
    assert codes == []


def test_doctor_does_not_call_an_empty_report_untriaged(
    tmp_path: Path, completed_run: SimpleNamespace
) -> None:
    report_file = _write(
        tmp_path / "nothing.json",
        {
            "schema": 1,
            "target_class": "item",
            "target": completed_run.item,
            "nothing_to_report": True,
            "what_happened": "",
            "expected": "",
            "observed": "",
            "impact": "low",
            "evidence": [],
        },
    )
    filed = _report(
        completed_run.store, completed_run.item, completed_run.run, report_file
    )
    assert is_ok(filed), filed
    codes = _doctor_codes(completed_run.store)
    assert "report_missing" not in codes
    assert "report_untriaged" not in codes


def _doctor_codes(store: Path) -> list[str]:
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    return [issue["code"] for issue in result["value"]["issues"]]


def test_a_report_file_with_duplicate_keys_refuses(completed_run, tmp_path) -> None:
    from context_rail.gateway.gateway import invoke

    path = tmp_path / "duplicate.json"
    path.write_text('{"schema": 1, "schema": 1}', encoding="utf-8", newline="\n")

    result = invoke(
        "report",
        {
            "item": completed_run.item,
            "run": completed_run.run,
            "report_file": str(path),
        },
        store=completed_run.store,
        actor="cto",
    )

    assert result["ok"] is False, result
    assert result["error"]["code"] == "VALIDATION_FAILED"
