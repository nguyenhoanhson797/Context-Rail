"""Approval lockout: nothing executes before the human approves the picture."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import EXECUTION_TOOLS, invoke
from helpers import init_store

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


_OK_COMMAND = [sys.executable, "-c", "print('ok')"]


def _declare(
    store: Path,
    tmp_path: Path,
    target: str,
    slug: str = "smoke",
    command: list[str] | None = None,
) -> None:
    spec = tmp_path / f"{slug}.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "cmd",
                "acceptance": False,
                "cmd": command or _OK_COMMAND,
                "inputs": ["src/**/*.py"],
                "timeout_s": 60,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    result = invoke(
        "check",
        {"slug": slug, "target": target, "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result


def _scaffold(tmp_path: Path, command: list[str] | None = None) -> tuple[Path, str]:
    store = init_store(tmp_path)
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    _declare(store, tmp_path, "login-page", command=command)
    return store, "login-page"


def test_verify_without_a_charter_runs_no_command(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
    ]
    store, _item = _scaffold(tmp_path, command=command)

    refused = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE", refused
    assert refused["error"]["details"]["reason"] == "approval_missing"
    assert not marker.exists()


def test_a_corrupt_charter_still_locks_verification_out(
    approved_store: Path, tmp_path: Path
) -> None:
    store = approved_store
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    _declare(store, tmp_path, "login-page")
    (store / "charter.md").write_text("not frontmatter\n", encoding="utf-8")

    refused = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE", refused
    assert refused["error"]["details"]["reason"] == "approval_missing"


def test_an_approved_charter_still_lets_verification_run(
    approved_store: Path, tmp_path: Path
) -> None:
    store = approved_store
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    _declare(store, tmp_path, "login-page")

    verified = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert is_ok(verified), verified


@pytest.mark.parametrize(
    "tool,args",
    [
        ("dispatch", {"item": "login-page", "expect": "0" * 16, "key": "k"}),
        ("review", {"item": "login-page", "run": "01JRUN000000000000000001", "key": "k"}),
        (
            "merge",
            {
                "item": "login-page",
                "run": "01JRUN000000000000000001",
                "expect": "0" * 16,
                "key": "k",
            },
        ),
        ("cto tick", {"key": "k"}),
    ],
)
def test_every_executor_refuses_without_a_charter(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    store, _item = _scaffold(tmp_path)

    refused = invoke(tool, args, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE", refused
    assert refused["error"]["details"]["reason"] == "approval_missing"
