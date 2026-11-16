"""A report can bind to a recorded candidate, and triage weighs the candidate."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from helpers import latest_record, records_of, refusal, run_git

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke

FINGERPRINT = "item|app|the value is two|the value is one"


def commit_change(root: Path, text: str) -> str:
    (root / "src" / "app.py").write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", "src/app.py")
    run_git(root, "commit", "-m", "change")
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def record_candidate(leaf_item: SimpleNamespace, text: str) -> str:
    tip = commit_change(leaf_item.root, text)
    recorded = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, "model": "model-a"},
        store=leaf_item.store,
        actor="dev",
    )
    assert is_ok(recorded), recorded
    return tip


def report_file(tmp_path: Path, name: str = "report.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "target_class": "item",
                "target": "app",
                "nothing_to_report": False,
                "what_happened": "the value was wrong",
                "expected": "the value is two",
                "observed": "the value is one",
                "impact": "medium",
                "evidence": [],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def file_report(leaf_item: SimpleNamespace, path: Path, **args: Any) -> dict[str, Any]:
    return invoke(
        "report",
        {"report_file": str(path), **args},
        store=leaf_item.store,
        actor="dev",
    )


def test_report_needs_an_item_when_no_run_is_named(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    refused = file_report(leaf_item, report_file(tmp_path))

    assert refusal(refused)[0] == "VALIDATION_FAILED"
    assert records_of(leaf_item.store, "report.filed") == []


def test_report_binds_to_a_recorded_candidate(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    tip = record_candidate(leaf_item, "VALUE = 2\n")
    candidate_id = records_of(leaf_item.store, "candidate.recorded")[0]["id"]

    filed = file_report(leaf_item, report_file(tmp_path), item=leaf_item.slug, candidate=tip)

    assert is_ok(filed), filed
    data = latest_record(leaf_item.store, "report.filed")["data"]
    assert data["candidate"] == candidate_id
    assert data["run"] is None
    assert data["item"] == leaf_item.slug


def test_report_resolves_a_revision_name(leaf_item: SimpleNamespace, tmp_path: Path) -> None:
    record_candidate(leaf_item, "VALUE = 2\n")

    filed = file_report(leaf_item, report_file(tmp_path), item=leaf_item.slug, candidate="HEAD")

    assert is_ok(filed), filed


def test_report_for_an_unrecorded_candidate_is_refused(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    tip = commit_change(leaf_item.root, "VALUE = 3\n")

    refused = file_report(leaf_item, report_file(tmp_path), item=leaf_item.slug, candidate=tip)

    assert refusal(refused)[0] == "NOT_FOUND"
    assert records_of(leaf_item.store, "report.filed") == []


def test_one_report_per_candidate(leaf_item: SimpleNamespace, tmp_path: Path) -> None:
    tip = record_candidate(leaf_item, "VALUE = 2\n")
    first = file_report(
        leaf_item, report_file(tmp_path), item=leaf_item.slug, candidate=tip, key="first"
    )
    assert is_ok(first), first

    second = file_report(
        leaf_item, report_file(tmp_path, "again.json"), item=leaf_item.slug, candidate=tip, key="again"
    )

    assert refusal(second)[0] == "CONFLICT"
    assert len(records_of(leaf_item.store, "report.filed")) == 1


def test_triage_doubles_the_weight_of_a_rejected_candidate(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    first_tip = record_candidate(leaf_item, "VALUE = 2\n")
    second_tip = record_candidate(leaf_item, "VALUE = 3\n")
    notes_file = tmp_path / "reject-review.md"
    notes_file.write_text(
        "## Summary\nRejected.\n\n## Findings\nFails review.\n\n## Evidence\nCheck.\n\n## Verdict\nReject.\n",
        encoding="utf-8",
        newline="\n",
    )
    reviewed = invoke(
        "review",
        {
            "item": leaf_item.slug,
            "commit": second_tip,
            "verdict": "reject",
            "model": "model-b",
            "notes_file": str(notes_file),
        },
        store=leaf_item.store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed
    for number, tip in enumerate((first_tip, second_tip)):
        filed = file_report(
            leaf_item,
            report_file(tmp_path, f"r{number}.json"),
            item=leaf_item.slug,
            candidate=tip,
            key=f"k-report-{number}",
        )
        assert is_ok(filed), filed

    triaged = invoke("triage", {"key": "k-triage"}, store=leaf_item.store, actor="cto")

    assert is_ok(triaged), triaged
    assert latest_record(leaf_item.store, "report.triaged")["data"]["weight"] == 3
