"""The done gate reads the records an agent's own tool left: candidates, checks, reviews."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import records_of, refusal, run_git, write_record

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


@pytest.fixture
def picture_answers(
    picture_answers: dict[str, Any], request: pytest.FixtureRequest
) -> dict[str, Any]:
    if getattr(request, "param", None) == "model-optional":
        picture_answers["charter"]["reviewer_different_model"] = False
    return picture_answers

def commit_change(root: Path, text: str) -> str:
    (root / "src" / "app.py").write_text(text, encoding="utf-8", newline="\n")
    (root / "fake-droid-work.txt").write_text("work\n", encoding="utf-8", newline="\n")
    run_git(root, "add", "src/app.py", "fake-droid-work.txt")
    run_git(root, "commit", "-m", "change")
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def candidate(leaf_item: SimpleNamespace, *, actor: str = "dev", **args: Any) -> str:
    tip = commit_change(leaf_item.root, "VALUE = 2\n")
    recorded = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, **args},
        store=leaf_item.store,
        actor=actor,
    )
    assert is_ok(recorded), recorded
    return tip


def move_to(leaf_item: SimpleNamespace, status: str) -> dict[str, Any]:
    shown = invoke("show", {"slug": leaf_item.slug}, store=leaf_item.store)
    assert is_ok(shown), shown
    return invoke(
        "move",
        {
            "slug": leaf_item.slug,
            "status": status,
            "expect": shown["value"]["doc"]["etag"],
        },
        store=leaf_item.store,
        actor="cto",
    )


def move_done(leaf_item: SimpleNamespace) -> dict[str, Any]:
    started = move_to(leaf_item, "doing")
    assert is_ok(started), started
    return move_to(leaf_item, "done")

def run_checks(leaf_item: SimpleNamespace) -> None:
    checked = invoke("verify", {"target": leaf_item.slug}, store=leaf_item.store, actor="cto")
    assert is_ok(checked), checked
    assert checked["value"]["passed"] is True, checked


def test_move_done_binds_gate_to_latest_candidate(leaf_item: SimpleNamespace) -> None:
    tip = candidate(leaf_item, model="model-a")
    run_checks(leaf_item)

    refused = move_done(leaf_item)

    assert refusal(refused)[0] == "BUSINESS_RULE_VIOLATED"
    details = refused["error"]["details"]
    assert details["candidate"] == tip
    assert "no review was recorded" in details["reasons"]


def test_move_done_opens_after_an_approving_review(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")
    run_checks(leaf_item)
    reviewed = invoke(
        "review",
        {
            "item": leaf_item.slug,
            "commit": "HEAD",
            "verdict": "approve",
            "model": "model-b",
        },
        store=leaf_item.store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed
    run_checks(leaf_item)

    moved = move_done(leaf_item)

    assert is_ok(moved), moved
    assert records_of(leaf_item.store, "review.verdict")


def test_move_done_refused_when_a_newer_candidate_supersedes_the_approval(
    leaf_item: SimpleNamespace,
) -> None:
    approved = candidate(leaf_item, model="model-a")
    run_checks(leaf_item)
    reviewed = invoke(
        "review",
        {"item": leaf_item.slug, "commit": "HEAD", "verdict": "approve", "model": "model-b"},
        store=leaf_item.store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed
    (leaf_item.root / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8", newline="\n")
    run_git(leaf_item.root, "add", "src/app.py")
    run_git(leaf_item.root, "commit", "-m", "later change")
    newer = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": "HEAD", "model": "model-a"},
        store=leaf_item.store,
        actor="dev",
    )
    assert is_ok(newer), newer
    run_checks(leaf_item)

    refused = move_done(leaf_item)

    assert refusal(refused)[0] == "BUSINESS_RULE_VIOLATED"
    reasons = refused["error"]["details"]["reasons"]
    assert any(approved in reason and "not" in reason for reason in reasons), reasons


def test_move_done_refused_after_a_rejecting_review(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")
    run_checks(leaf_item)
    reviewed = invoke(
        "review",
        {"item": leaf_item.slug, "commit": "HEAD", "verdict": "reject", "model": "model-b"},
        store=leaf_item.store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed

    refused = move_done(leaf_item)

    assert refusal(refused)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("reject" in reason for reason in refused["error"]["details"]["reasons"])


@pytest.mark.parametrize("picture_answers", ["model-optional"], indirect=True)
def test_a_review_without_models_opens_the_gate_when_the_rule_is_off(
    leaf_item: SimpleNamespace,
) -> None:
    candidate(leaf_item)
    run_checks(leaf_item)
    reviewed = invoke(
        "review",
        {"item": leaf_item.slug, "commit": "HEAD", "verdict": "approve"},
        store=leaf_item.store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed
    run_checks(leaf_item)

    assert is_ok(move_done(leaf_item))


def modelless_review(leaf_item: SimpleNamespace) -> None:
    write_record(
        leaf_item.store,
        "01JREV00000000000000000001",
        "review.verdict",
        {
            "schema": 1,
            "item": leaf_item.slug,
            "reviewer_actor": "qa",
            "implementer_actor": "dev",
            "candidate_commit": "a" * 40,
            "verdict": "approve",
        },
        about=(leaf_item.slug,),
    )


def review_codes(leaf_item: SimpleNamespace) -> set[str]:
    issues = invoke("doctor", {}, store=leaf_item.store, actor="human")["value"]["issues"]
    return {issue["code"] for issue in issues}


def test_doctor_flags_a_review_without_models_when_the_rule_is_on(
    leaf_item: SimpleNamespace,
) -> None:
    modelless_review(leaf_item)

    assert "review_invalid" in review_codes(leaf_item)


@pytest.mark.parametrize("picture_answers", ["model-optional"], indirect=True)
def test_doctor_review_invalid_ignores_missing_models_when_rule_off(
    leaf_item: SimpleNamespace,
) -> None:
    modelless_review(leaf_item)

    assert "review_invalid" not in review_codes(leaf_item)