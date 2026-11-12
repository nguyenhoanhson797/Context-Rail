"""The whole loop with no agent process: a tool commits, the rail records, gates and finishes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import (
    as_human,
    as_seat,
    open_test_session,
    records_of,
    refusal,
    restrict_code_write,
    run_git,
    start_repo,
    write_record,
)

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.model.seats import SEAT_GRANTED


def etag_of(item: SimpleNamespace) -> str:
    shown = invoke("show", {"slug": item.slug}, store=item.store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def move(item: SimpleNamespace, status: str, actor: str = "cto") -> dict[str, Any]:
    return invoke(
        "move",
        {"slug": item.slug, "status": status, "expect": etag_of(item)},
        store=item.store,
        actor=actor,
    )


def work_in_a_worktree(item: SimpleNamespace, tmp_path: Path) -> tuple[Path, str]:
    """What any coding tool does on its own: a branch in its own worktree, one commit."""
    path = tmp_path / "work-tree"
    run_git(item.root, "worktree", "add", str(path), "-b", "work")
    (path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    (path / "fake-droid-work.txt").write_text("work\n", encoding="utf-8", newline="\n")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-m", "implement the item")
    return path, run_git(path, "rev-parse", "HEAD").stdout.strip()


def test_name_mode_item_reaches_done_without_any_agent(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    store = leaf_item.store
    brief = invoke("brief", {"item": leaf_item.slug}, store=store, actor="cto")
    assert is_ok(brief), brief
    assert brief["value"]["role"] == "implementer"
    assert "rail loop candidate" in brief["value"]["sections"]["recording"]

    worktree, tip = work_in_a_worktree(leaf_item, tmp_path)
    candidate = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": "work", "model": "model-a", "harness": "any-tool"},
        store=store,
        actor="dev",
    )
    assert is_ok(candidate), candidate
    assert candidate["value"]["commit"] == tip

    verified = invoke(
        "verify",
        {"target": leaf_item.slug, "worktree": str(worktree)},
        store=store,
        actor="cto",
    )
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True

    review_brief = invoke(
        "brief", {"item": leaf_item.slug, "role": "reviewer", "commit": tip}, store=store, actor="cto"
    )
    assert is_ok(review_brief), review_brief
    assert review_brief["value"]["role"] == "reviewer"

    reviewed = invoke(
        "review",
        {"item": leaf_item.slug, "commit": "work", "verdict": "approve", "model": "model-b"},
        store=store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed

    assert is_ok(move(leaf_item, "doing"))
    stale = move(leaf_item, "done")
    assert refusal(stale)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("stale" in reason for reason in stale["error"]["details"]["reasons"])

    run_git(leaf_item.root, "merge", "--ff-only", "work")
    rechecked = invoke("verify", {"target": leaf_item.slug}, store=store, actor="cto")
    assert is_ok(rechecked), rechecked
    finished = move(leaf_item, "done")
    assert is_ok(finished), finished

    report_file = tmp_path / "report.json"
    report_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "target_class": "item",
                "target": leaf_item.slug,
                "nothing_to_report": True,
                "what_happened": "",
                "expected": "",
                "observed": "",
                "impact": "low",
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    reported = invoke(
        "report",
        {"item": leaf_item.slug, "report_file": str(report_file)},
        store=store,
        actor="cto",
    )
    assert is_ok(reported), reported

    ledger = invoke("ledger", {"item": leaf_item.slug}, store=store, actor="cto")
    assert is_ok(ledger), ledger
    assert [row["attempt"] for row in ledger["value"]["candidates"]] == [1]

    overview = invoke("overview", {"max_chars": 20000}, store=store, actor="cto")
    assert is_ok(overview), overview
    waiting = [
        line
        for line in overview["value"]["text"].splitlines()
        if leaf_item.slug in line and "human" in line.lower() and "wait" in line.lower()
    ]
    assert waiting == []


def grant(seated: SimpleNamespace, seat: str, role: str, lead: str) -> None:
    write_record(
        seated.store,
        f"seat-{seat}",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": seat,
            "role": role,
            "anchor": seated.task,
            "granted_by": lead,
        },
        at="2026-11-10T10:05:00Z",
        actor=lead,
    )


def test_role_mode_item_reaches_done_with_seats(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    start_repo(seated)
    grant(seated, "rev-1", "reviewer", role_store.lead)
    path = Path(seated.root) / "src" / "app.py"
    path.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(seated.root, "add", "src/app.py")
    run_git(seated.root, "commit", "-m", "change")

    candidate = as_seat(
        seated.store,
        seated.seat,
        "candidate",
        {"item": seated.task, "commit": "HEAD", "model": "model-a"},
    )
    assert is_ok(candidate), candidate

    own_review = as_seat(
        seated.store,
        seated.seat,
        "review",
        {"item": seated.task, "commit": "HEAD", "verdict": "approve", "model": "model-b"},
    )
    assert refusal(own_review) == ("PERMISSION_DENIED", "method_not_in_role")

    reviewed = as_seat(
        seated.store,
        "rev-1",
        "review",
        {"item": seated.task, "commit": "HEAD", "verdict": "approve", "model": "model-b"},
    )
    assert is_ok(reviewed), reviewed
    assert [r["data"]["reviewer_actor"] for r in records_of(seated.store, "review.verdict")] == [
        "rev-1"
    ]


def test_no_process_spawned_but_git_and_checks(
    leaf_item: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    started: list[str] = []
    real = subprocess.Popen

    class Spy(real):  # type: ignore[valid-type, misc]
        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            first = args if isinstance(args, str) else args[0]
            started.append(Path(str(first)).stem.lower())
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    worktree, _ = work_in_a_worktree(leaf_item, tmp_path)
    store = leaf_item.store
    assert is_ok(invoke("brief", {"item": leaf_item.slug}, store=store, actor="cto"))
    assert is_ok(
        invoke("candidate", {"item": leaf_item.slug, "commit": "work"}, store=store, actor="dev")
    )
    assert is_ok(
        invoke(
            "verify",
            {"target": leaf_item.slug, "worktree": str(worktree)},
            store=store,
            actor="cto",
        )
    )

    allowed = {"git", Path(sys.executable).stem.lower()}
    assert started, "the run started no process at all"
    assert {name for name in started if name not in allowed} == set()


def test_multi_manager_seats_keep_to_their_reach_through_the_record_loop(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    store = role_store.store
    orders_token = open_test_session(store, role_store.orders_seat)
    billing_token = open_test_session(store, role_store.billing_seat)

    def orders(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        with caller_identity(None, None, session=orders_token):
            return invoke(name, args or {}, store=store)

    with caller_identity(None, None, session=billing_token):
        billing_queue = invoke("next", {}, store=store)
    assert is_ok(billing_queue), billing_queue
    assert set(billing_queue["value"]["queue"]) <= {role_store.b1, role_store.b2}

    assert refusal(orders("show", {"slug": role_store.b1})) == (
        "PERMISSION_DENIED",
        "out_of_reach",
    )
    assert refusal(
        orders("brief", {"item": role_store.b1, "role": "implementer"})
    )[0] == "PERMISSION_DENIED"

    start_repo(seated)
    restrict_code_write(store, ["src/**"])
    (Path(seated.root) / "src" / "ok.py").write_text("OK = 1\n", encoding="utf-8")
    (Path(seated.root) / "docs" / "x.md").write_text("changed\n", encoding="utf-8")
    run_git(seated.root, "add", "-A")
    run_git(seated.root, "commit", "-m", "both areas")
    outside = as_seat(
        store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )
    assert refusal(outside) == ("BUSINESS_RULE_VIOLATED", "code_reach")
    assert records_of(store, "candidate.recorded") == []

    with as_human():
        revoked = invoke("seat revoke", {"seat": role_store.lead}, store=store, actor=None)
    assert is_ok(revoked), revoked
    assert refusal(orders("show", {"slug": role_store.o1})) == (
        "PERMISSION_DENIED",
        "session_invalid",
    )
