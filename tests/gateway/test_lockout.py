"""Approval lockout: nothing executes before the human approves the picture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import EXECUTION_TOOLS, invoke

STORE = "context-rail"
MUST = "project/send-invoice.md"
GOAL = "ship-invoicing"


@pytest.fixture
def unapproved_store(tmp_path: Path, answers: Path) -> Path:
    result = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    return tmp_path / STORE


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _edit_body(store: Path, rel: str) -> None:
    path = store / rel
    path.write_text(path.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")


def test_active_and_done_moves_are_locked_before_approval(unapproved_store: Path) -> None:
    result = invoke(
        "move",
        {"slug": "send-invoice", "status": "doing", "expect": _etag(unapproved_store, "send-invoice")},
        store=unapproved_store,
        actor="cto",
    )

    assert result["error"]["code"] == "INVALID_STATE"
    assert result["error"]["details"]["reason"] == "approval_missing"


def test_a_mismatched_charter_locks_out_with_its_own_reason(
    approved_store: Path,
) -> None:
    _edit_body(approved_store, MUST)

    result = invoke(
        "move",
        {"slug": "send-invoice", "status": "doing", "expect": _etag(approved_store, "send-invoice")},
        store=approved_store,
        actor="cto",
    )

    assert result["error"]["code"] == "INVALID_STATE"
    assert result["error"]["details"]["reason"] == "approval_mismatch"


def test_planning_writes_stay_open_before_approval(unapproved_store: Path) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "Plan item"}, store=unapproved_store, actor="cto"
    )
    assert is_ok(added), added

    etag = _etag(unapproved_store, "plan-item")
    renamed = invoke(
        "update",
        {"slug": "plan-item", "set": ["estimate=2"], "expect": etag},
        store=unapproved_store,
        actor="cto",
    )
    assert is_ok(renamed), renamed

    linked = invoke(
        "link",
        {
            "src": "plan-item",
            "rel": "part_of",
            "dst": GOAL,
            "expect": _etag(unapproved_store, "plan-item"),
        },
        store=unapproved_store,
        actor="cto",
    )
    assert is_ok(linked), linked


def test_reads_stay_open_before_approval(unapproved_store: Path) -> None:
    for name, args in (
        ("show", {"slug": "charter"}),
        ("status", {}),
        ("find", {"preset": "scope"}),
        ("doctor", {}),
    ):
        assert is_ok(invoke(name, args, store=unapproved_store, actor="cto")), name


def test_the_execution_tools_are_pinned() -> None:
    assert set(EXECUTION_TOOLS) == {"verify", "dispatch", "review", "merge", "cto tick"}


def test_lockout_applies_to_every_actor(unapproved_store: Path) -> None:
    result = invoke(
        "move",
        {"slug": "send-invoice", "status": "doing", "expect": _etag(unapproved_store, "send-invoice")},
        store=unapproved_store,
        actor="human",
    )

    assert result["error"]["code"] == "INVALID_STATE"
    assert result["error"]["details"]["reason"] == "approval_missing"
