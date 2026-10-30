"""The digest: stuck and too-long Items, human waits, decisions and usage."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import run_cli, write_record, write_run

DIGEST_KEYS = {
    "summary",
    "needs_human",
    "reversible_decisions",
    "stuck",
    "too_long",
    "quality",
    "usage",
    "reports",
    "omitted",
}


def _digest(store, **params: object) -> dict:
    result = invoke("digest", params, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def _a_day_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_digest_flags_stuck_and_too_long(digest_store: SimpleNamespace) -> None:
    value = _digest(digest_store.store, since=_a_day_ago())
    assert set(value) == DIGEST_KEYS
    assert digest_store.stuck in [row["slug"] for row in value["stuck"]]
    assert digest_store.too_long in [row["slug"] for row in value["too_long"]]
    assert value["summary"]["text"]
    assert value["summary"]["counts"]["items"] > 0


def test_digest_surfaces_human_waits_and_reversible_decisions(
    digest_store: SimpleNamespace,
) -> None:
    value = _digest(digest_store.store, since=_a_day_ago())
    assert digest_store.stuck in [row["item"] for row in value["needs_human"]]
    decisions = value["reversible_decisions"]
    assert [row["class"] for row in decisions] == ["cto-reversible"]
    assert decisions[0]["before"] == {"depends_on": []}
    assert decisions[0]["after"] == {"depends_on": ["login-page"]}
    assert decisions[0]["reason"] == "reuses the login parser"
    assert value["usage"]["known_sum"]
    assert value["usage"]["unknown_attempts"]["factory_credits"] >= 1
    assert value["reports"]["untriaged"] >= 1
    assert value["reports"]["total"] >= 1


def test_brief_repeats_the_reversible_decision(digest_store: SimpleNamespace) -> None:
    text = invoke("cto brief", {}, store=digest_store.store, actor="cto")["value"]["text"]
    section = text[text.index("Reversible") :]
    assert "cto-reversible" in section
    assert "depends_on" in section


def test_digest_quality_separates_implementations_from_reviews(
    digest_store: SimpleNamespace,
) -> None:
    value = _digest(digest_store.store)
    quality = value["quality"]
    assert quality["runs"]["accepted"] >= 0
    assert quality["reviews"]["rejected"] >= 0
    assert isinstance(quality["rejected_reviews"], list)
    assert quality["checks"]["passed"] >= 0


def test_digest_never_runs_a_process(
    digest_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a digest never executes anything")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    assert is_ok(invoke("digest", {}, store=digest_store.store, actor="cto"))


def test_digest_bounds_its_lists(digest_store: SimpleNamespace) -> None:
    whole = _digest(digest_store.store, limit=20)
    bounded = _digest(digest_store.store, limit=1)
    assert len(bounded["reports"]["recent"]) <= 1
    assert bounded["omitted"]["reports"] == whole["reports"]["total"] - len(
        bounded["reports"]["recent"]
    )
    assert len(bounded["quality"]["rejected_reviews"]) <= 1


def test_cli_digest_names_the_stuck_and_too_long_items(
    digest_store: SimpleNamespace,
) -> None:
    completed = run_cli(
        digest_store.store.parent,
        "digest",
        "--since",
        _a_day_ago(),
        store=digest_store.store,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)["value"]
    assert digest_store.stuck in [row["slug"] for row in value["stuck"]]
    assert digest_store.too_long in [row["slug"] for row in value["too_long"]]
    assert value["summary"]["text"]


def _reject_review(store: Path, item: str, run: str, record_id: str, at: str) -> None:
    write_record(
        store,
        record_id,
        "review.verdict",
        {
            "schema": 1,
            "item": item,
            "implementation_run": run,
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": "b" * 40,
            "verdict": "reject",
            "findings": ["the form still accepts a bad password"],
            "evidence": [run],
            "profile_version": 1,
        },
        at=at,
        about=(item,),
    )


def test_digest_reads_implementation_runs_not_the_newest_run_row(
    seeded_runs_store: SimpleNamespace,
) -> None:
    """A reviewer's row keeps ``accepted`` null; its verdict is not the outcome."""
    store = seeded_runs_store.store
    item = seeded_runs_store.item
    write_run(
        store,
        "01JREVIEWERRUN00000000001",
        item=item,
        role="reviewer",
        at="2026-10-28T10:30:00Z",
    )
    _reject_review(
        store,
        item,
        seeded_runs_store.run,
        "01JREJECTREVIEW0000000001",
        "2026-10-28T10:31:00Z",
    )
    value = _digest(store, since="2026-10-28T00:00:00Z")
    quality = value["quality"]
    assert quality["runs"] == {"accepted": 1, "rejected": 0, "pending": 0}
    assert quality["reviews"] == {"approved": 1, "rejected": 1}
    assert [row["review"] for row in quality["rejected_reviews"]] == [
        "01JREJECTREVIEW0000000001"
    ]
    rows = invoke("ledger", {"item": item}, store=store, actor="cto")["value"]["runs"]
    reviewer = [row for row in rows if row["role"] == "reviewer"][0]
    assert reviewer["accepted"] is None


def test_reversible_decisions_read_the_class_from_the_record_payload(
    digest_store: SimpleNamespace,
) -> None:
    write_record(
        digest_store.store,
        "01JHUMANONLYDECISION000001",
        "decision.made",
        {
            "schema": 1,
            "decision_class": "human-only",
            "reason": "the reminder copy is the payer's own wording",
            "before": {"depends_on": []},
            "after": {"depends_on": ["payer-copy"]},
        },
        at=_a_day_ago(),
        about=(digest_store.stuck,),
    )
    value = _digest(digest_store.store, since=_a_day_ago())
    assert [row["class"] for row in value["reversible_decisions"]] == ["cto-reversible"]


def _human_wait(store: Path, record_id: str, *, item: str, at: str) -> None:
    write_record(
        store,
        record_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": "human-wait",
            "target": f"decision {record_id}",
            "item": item,
            "run": None,
            "nothing_to_report": False,
            "what_happened": "the reminder copy needs a human decision",
            "expected": "a human decides the copy",
            "observed": "the copy is undecided",
            "impact": "medium",
            "evidence": [],
            "fingerprint": f"human-wait|{record_id}",
        },
        at=at,
        about=(item,),
    )


def _reversible_decision(store: Path, record_id: str, *, at: str) -> None:
    write_record(
        store,
        record_id,
        "decision.made",
        {
            "schema": 1,
            "decision_class": "cto-reversible",
            "reason": "reuses the login parser",
            "before": {"depends_on": []},
            "after": {"depends_on": ["login-page"]},
        },
        at=at,
        about=("sign-in",),
    )


def _cto_rejection(store: Path, record_id: str, *, run: str, item: str, at: str) -> None:
    write_record(
        store,
        record_id,
        "review.verdict",
        {
            "schema": 1,
            "item": item,
            "implementation_run": run,
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": "b" * 40,
            "verdict": "reject",
            "findings": ["no"],
            "evidence": [],
            "profile_version": 1,
        },
        at=at,
        about=(item,),
    )


def test_a_human_wait_outside_the_newest_report_page_still_surfaces(
    digest_store: SimpleNamespace,
) -> None:
    """The waits are queried on their own bound, never filtered out of a page."""
    store = digest_store.store
    wait_id = "01JHUMANWAIT000000000000001"
    for index in range(25):
        _human_wait_later(store, f"01JNEWERREPORT{index:013d}", at=_iso_minutes(25 - index))

    value = _digest(store, limit=20)

    assert wait_id in [row["report"] for row in value["needs_human"]]
    assert value["omitted"]["needs_human"] == 0


def test_human_waits_report_a_truthful_omission_count(
    digest_store: SimpleNamespace,
) -> None:
    store = digest_store.store
    for index in range(3):
        _human_wait(store, f"01JWAITCOUNT{index:013d}", item=digest_store.stuck, at=_iso_minutes(10 + index))
    value = _digest(store, limit=2)
    shown = [row["report"] for row in value["needs_human"]]
    assert len(shown) <= 2
    assert value["omitted"]["needs_human"] == 2


def test_reversible_decisions_and_rejections_report_a_real_omission(
    seeded_runs_store: SimpleNamespace,
) -> None:
    store = seeded_runs_store.store
    for index in range(3):
        _reversible_decision(store, f"01JREVERSIBLE{index:013d}", at=_iso_minutes(10 + index))
        _cto_rejection(
            store,
            f"01JREJECTION{index:014d}",
            run=seeded_runs_store.run,
            item=seeded_runs_store.item,
            at=_iso_minutes(10 + index),
        )
    value = _digest(store, since="2026-10-28T00:00:00Z", limit=2)
    assert len(value["reversible_decisions"]) == 2
    assert value["omitted"]["reversible_decisions"] == 1
    assert len(value["quality"]["rejected_reviews"]) == 2
    assert value["omitted"]["rejected_reviews"] == 1


def _iso_minutes(minutes: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _human_wait_later(store: Path, record_id: str, *, at: str) -> None:
    write_record(
        store,
        record_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": "environment",
            "target": f"later {record_id}",
            "item": "login-page",
            "run": None,
            "nothing_to_report": False,
            "what_happened": "a later report pushes the wait off the page",
            "expected": "the page holds every report",
            "observed": "it does not",
            "impact": "low",
            "evidence": [],
            "fingerprint": f"environment|{record_id}",
        },
        at=at,
        about=("login-page",),
    )

