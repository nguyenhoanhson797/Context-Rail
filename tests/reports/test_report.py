"""A report is one structured Record about an item."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.kernel.triage import report_fingerprint
from helpers import records_of

REPORT_KEYS = {
    "schema",
    "target_class",
    "target",
    "item",
    "run",
    "candidate",
    "nothing_to_report",
    "what_happened",
    "expected",
    "observed",
    "impact",
    "evidence",
    "fingerprint",
}


def _report(
    store: Path, item: str, report_file: Path, key: str | None = None
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "item": item,
        "report_file": str(report_file),
    }
    if key is not None:
        args["key"] = key
    return invoke("report", args, store=store, actor="cto")


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path




def test_report_data_carries_exactly_the_pinned_keys(
    completed_run: SimpleNamespace,
) -> None:
    result = _report(
        completed_run.store, completed_run.item, completed_run.report_file
    )
    assert is_ok(result), result
    assert set(result["value"]) == {"report", "nothing_to_report"}
    record = records_of(completed_run.store, "report.filed")[0]
    data = record["data"]
    assert set(data) == REPORT_KEYS
    assert data["schema"] == 1
    assert data["item"] == completed_run.item
    assert data["run"] is None
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
        completed_run.report_file,
        key="k-replay",
    )
    assert is_ok(first), first
    second = _report(
        completed_run.store,
        completed_run.item,
        completed_run.report_file,
        key="k-replay",
    )
    assert is_ok(second), second
    assert second["value"] == first["value"]
    assert len(records_of(completed_run.store, "report.filed")) == 1




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
        completed_run.store, completed_run.item, report_file
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
        completed_run.store, completed_run.item, report_file
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
        completed_run.store, completed_run.item, report_file
    )
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(completed_run.store, "report.filed") == []


def test_doctor_flags_an_untriaged_report(completed_run: SimpleNamespace) -> None:
    assert "report_untriaged" not in _doctor_codes(completed_run.store)

    filed = _report(completed_run.store, completed_run.item, completed_run.report_file)
    assert is_ok(filed), filed
    assert "report_untriaged" in _doctor_codes(completed_run.store)


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
        completed_run.store, completed_run.item, report_file
    )
    assert is_ok(filed), filed
    codes = _doctor_codes(completed_run.store)
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
            "report_file": str(path),
        },
        store=completed_run.store,
        actor="cto",
    )

    assert result["ok"] is False, result
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_report_refuses_a_ghost_item(completed_run: SimpleNamespace) -> None:
    refused = _report(completed_run.store, "no-such-item", completed_run.report_file)

    assert refused["error"]["code"] == "NOT_FOUND", refused
    assert refused["error"]["details"]["reason"] == "item_unknown"
    assert records_of(completed_run.store, "report.filed") == []
