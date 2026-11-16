"""One scrum store runs a sprint and a bugfix workflow at the same time."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import approve_picture, approve_store, init_store, record_result_for_test, run_git


def _add(store: Path, preset: str, title: str, rel: list[str] | None = None) -> dict:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or []},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result
    return result["value"]


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _declare_proof(store: Path, tmp_path: Path, slug: str) -> None:
    """Declare and pass the two Checks the bugfix workflow requires to close.

    A Check proves its inputs against a git worktree, so the project is committed
    before the verdicts are recorded.
    """
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    run_git(tmp_path, "init")
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-m", "picture")
    (tmp_path / "fix.txt").write_text("fixed\n", encoding="utf-8", newline="\n")
    run_git(tmp_path, "add", "fix.txt")
    run_git(tmp_path, "commit", "-m", "fix")
    for name, acceptance in (("repro-check", False), ("acceptance", True)):
        spec = tmp_path / f"{name}.json"
        spec.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "mode": "cmd",
                    "acceptance": acceptance,
                    "cmd": [sys.executable, "-c", "pass"],
                    "inputs": ["README.md"],
                    "timeout_s": 60,
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        args = {
            "slug": name,
            "target": slug,
            "spec_file": str(spec),
            "expect": "absent",
        }
        if acceptance:
            args["confirm"] = f"APPROVE {name}"
        declared = invoke(
            "check",
            args,
            store=store,
            actor="human" if acceptance else "cto",
        )
        assert is_ok(declared), declared
    # An acceptance Check joins the protected set, so the picture is approved again.
    approved = approve_picture(store)
    assert is_ok(approved), approved
    for name in ("repro-check", "acceptance"):
        ran = invoke("verify", {"target": name}, store=store, actor="cto")
        assert is_ok(ran), ran
        assert ran["value"]["passed"] is True, ran


def _move(store: Path, slug: str, status: str) -> dict:
    return invoke(
        "move",
        {"slug": slug, "status": status, "expect": _etag(store, slug)},
        store=store,
        actor="cto",
    )


def test_a_sprint_and_a_bug_run_their_own_workflows(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    approve_store(store)
    sprint = _add(store, "sprint", "Sprint 3")
    bug = _add(store, "bug", "Checkout crash", ["part_of=sprint-3"])
    task = _add(store, "task", "Implement checkout", ["part_of=sprint-3"])

    shown = invoke("show", {"slug": "checkout-crash"}, store=store)
    assert shown["value"]["doc"]["status"] == "reported"
    assert shown["value"]["card"]["workflow"] == "bugfix"
    assert shown["value"]["card"]["next"] == ["closed", "fixing"]

    started = invoke(
        "do",
        {"action": "start-sprint", "item": sprint["slug"], "etag": _etag(store, sprint["slug"])},
        store=store,
        actor="cto",
    )
    assert is_ok(started), started

    for status in ("fixing", "verifying"):
        moved = _move(store, bug["slug"], status)
        assert is_ok(moved), moved
    _declare_proof(store, tmp_path, bug["slug"])
    assert is_ok(invoke("candidate", {"item": bug["slug"], "commit": "HEAD"}, store=store, actor="cto"))
    record_result_for_test(
        store,
        bug["slug"],
        actor="cto",
        outcome="completed",
        commit=invoke(
            "ledger",
            {"item": bug["slug"]},
            store=store,
            actor="cto",
        )["value"]["candidates"][0]["commit"],
    )
    moved = _move(store, bug["slug"], "closed")
    assert is_ok(moved), moved

    refused = invoke(
        "do",
        {"action": "close-sprint", "item": sprint["slug"], "etag": _etag(store, sprint["slug"])},
        store=store,
        actor="cto",
    )
    assert refused["ok"] is False
    assert refused["error"]["details"]["condition"] == "children"

    for status in ("doing", "done"):
        if status == "done":
            record_result_for_test(store, task["slug"], actor="cto")
        moved = _move(store, task["slug"], status)
        assert is_ok(moved), moved

    record_result_for_test(store, sprint["slug"], actor="cto")
    closed = invoke(
        "do",
        {"action": "close-sprint", "item": sprint["slug"], "etag": _etag(store, sprint["slug"])},
        store=store,
        actor="cto",
    )
    assert is_ok(closed), closed

    report = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(report), report
    assert report["value"]["issues"] == []
