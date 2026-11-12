"""A candidate over budget is recorded and flagged, never refused."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import records_of, run_git

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


@pytest.fixture
def picture_answers(picture_answers: dict[str, Any]) -> dict[str, Any]:
    picture_answers["charter"]["budgets"]["item"]["attempts"] = 1
    picture_answers["charter"]["budgets"]["item"]["input_tokens"] = 100
    return picture_answers


def commit_change(root: Path, text: str) -> str:
    (root / "src" / "app.py").write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", "src/app.py")
    run_git(root, "commit", "-m", "change")
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def record(leaf_item: SimpleNamespace, text: str, **args: Any) -> dict[str, Any]:
    tip = commit_change(leaf_item.root, text)
    return invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, **args},
        store=leaf_item.store,
        actor="dev",
    )


def test_first_candidate_at_the_cap_is_near_not_exceeded(leaf_item: SimpleNamespace) -> None:
    recorded = record(leaf_item, "VALUE = 2\n")

    assert is_ok(recorded), recorded
    assert recorded["value"]["budget"]["state"] == "near"


def test_candidate_over_budget_is_recorded_and_flagged(leaf_item: SimpleNamespace) -> None:
    assert is_ok(record(leaf_item, "VALUE = 2\n"))

    second = record(leaf_item, "VALUE = 3\n")

    assert is_ok(second), second
    budget = second["value"]["budget"]
    assert budget["state"] == "exceeded"
    assert any("attempts" in reason for reason in budget["reasons"])
    assert len(records_of(leaf_item.store, "candidate.recorded")) == 2


def test_candidate_usage_counts_against_a_named_counter(leaf_item: SimpleNamespace) -> None:
    recorded = record(leaf_item, "VALUE = 2\n", usage=["input_tokens=150"])

    assert is_ok(recorded), recorded
    reasons = recorded["value"]["budget"]["reasons"]
    assert any("input_tokens" in reason for reason in reasons)


def test_ci_fails_on_the_warning_only_when_asked(leaf_item: SimpleNamespace) -> None:
    assert is_ok(record(leaf_item, "VALUE = 2\n"))
    assert is_ok(record(leaf_item, "VALUE = 3\n"))

    plain = invoke("ci", {}, store=leaf_item.store, actor="cto")
    strict = invoke("ci", {"warnings_as_errors": True}, store=leaf_item.store, actor="cto")

    assert is_ok(plain), plain
    assert not is_ok(strict), strict
    codes = [issue["code"] for issue in strict["error"]["details"]["issues"]]
    assert "budget_exceeded" in codes


def test_unreported_usage_is_unknown_not_zero(leaf_item: SimpleNamespace) -> None:
    recorded = record(leaf_item, "VALUE = 2\n")

    unknown = recorded["value"]["budget"]["unknown"]
    assert unknown["input_tokens"] == 1
    assert unknown["seconds"] == 1