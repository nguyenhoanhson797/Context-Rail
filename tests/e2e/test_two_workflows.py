"""One scrum store runs a sprint and a bugfix workflow at the same time."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import init_store


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


def _move(store: Path, slug: str, status: str) -> dict:
    return invoke(
        "move",
        {"slug": slug, "status": status, "expect": _etag(store, slug)},
        store=store,
        actor="cto",
    )


def test_a_sprint_and_a_bug_run_their_own_workflows(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
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

    for status in ("fixing", "verifying", "closed"):
        moved = _move(store, bug["slug"], status)
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
        moved = _move(store, task["slug"], status)
        assert is_ok(moved), moved

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
