"""The shipped verbs: basic and scrum actions load, run and refuse safely."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from helpers import init_store


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def test_scrum_ships_the_daily_verbs() -> None:
    pack = load_pack("scrum", Path("."))

    assert set(pack.actions) == {
        "start-sprint",
        "close-sprint",
        "file-bug",
        "triage-report",
    }
    assert all(action.use_when for action in pack.actions.values())


def test_basic_ships_start_close_and_next() -> None:
    pack = load_pack("basic", Path("."))

    assert set(pack.actions) == {"start", "close", "next-actions"}


def test_the_catalog_lists_the_actions(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")

    verbs = invoke("types", {}, store=store, actor="cto")
    catalog = invoke("explain", {"name": "start-sprint"}, store=store, actor="cto")

    assert is_ok(verbs), verbs
    assert is_ok(catalog), catalog
    # explain covers presets and workflows; actions appear through the catalog.
    from context_rail.context import surface
    from context_rail.gateway.gateway import Context
    from context_rail.model.manifest import load_manifest

    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("scrum", store),
        actor="cto",
    )
    listed = surface.verbs(context)
    assert {entry["name"] for entry in listed["items"]} == set(context.pack.actions)


def test_a_basic_task_can_start_and_close(tmp_path: Path) -> None:
    store = init_store(tmp_path, "basic")
    assert is_ok(invoke("add", {"preset": "task", "title": "A task"}, store=store, actor="cto"))

    started = invoke(
        "do",
        {"action": "start", "item": "a-task", "etag": _etag(store, "a-task")},
        store=store,
        actor="cto",
    )
    assert is_ok(started), started
    assert started["value"]["result"]["status"] == "doing"

    closed = invoke(
        "do",
        {"action": "close", "item": "a-task", "etag": _etag(store, "a-task")},
        store=store,
        actor="cto",
    )
    assert is_ok(closed), closed
    assert closed["value"]["result"]["status"] == "done"


def test_next_actions_is_read_only(tmp_path: Path) -> None:
    store = init_store(tmp_path, "basic")
    assert is_ok(invoke("add", {"preset": "task", "title": "A task"}, store=store, actor="cto"))

    result = invoke("do", {"action": "next-actions"}, store=store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["record"] is None
    assert "queue" in result["value"]["result"]


def test_close_sprint_refuses_an_open_child(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    assert is_ok(invoke("add", {"preset": "sprint", "title": "Sprint 3"}, store=store, actor="cto"))
    assert is_ok(
        invoke(
            "add",
            {"preset": "task", "title": "A task", "rel": ["part_of=sprint-3"]},
            store=store,
            actor="cto",
        )
    )
    started = invoke(
        "do",
        {"action": "start-sprint", "item": "sprint-3", "etag": _etag(store, "sprint-3")},
        store=store,
        actor="cto",
    )
    assert is_ok(started), started

    refused = invoke(
        "do",
        {"action": "close-sprint", "item": "sprint-3", "etag": _etag(store, "sprint-3")},
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["details"]["condition"] == "children"
    assert "a-task" in refused["error"]["details"]["open"]


def test_file_bug_creates_a_bug_with_a_parent(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    assert is_ok(invoke("add", {"preset": "sprint", "title": "Sprint 3"}, store=store, actor="cto"))

    created = invoke(
        "do",
        {
            "action": "file-bug",
            "set": ["title=Checkout crash", "parent=sprint-3"],
        },
        store=store,
        actor="cto",
    )

    assert is_ok(created), created
    assert created["value"]["result"]["path"].endswith("checkout-crash.md")
