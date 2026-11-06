"""The legacy snapshot is rebuilt from the pack, not from a v2 document model."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.ui import snapshot
from helpers import init_store


def _context(store: Path) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("scrum", store),
        actor="cto",
    )


def _add(store: Path, preset: str, title: str, rel: list[str] | None = None, **sets: str) -> dict:
    args: dict = {"preset": preset, "title": title, "rel": rel or []}
    if sets:
        args["set"] = [f"{key}={value}" for key, value in sets.items()]
    result = invoke("add", args, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_the_snapshot_carries_the_pack_vocabulary(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    context = _context(store)
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    assert value["pack"] == "scrum"
    assert "doing" in value["statuses"] and "closed" in value["statuses"]
    assert value["status_categories"]["closed"] == "done"
    assert {"sprint", "bugfix", "scope", "default"} <= {
        entry["name"] for entry in value["workflows"]
    }
    sprint = next(entry for entry in value["workflows"] if entry["name"] == "sprint")
    assert sprint["quality"]["done"]["gate"] == "item_done"


def test_containers_are_phases_and_work_is_a_task(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    _add(store, "sprint", "Sprint 4")
    _add(store, "task", "Wire checkout", ["part_of=sprint-4"])
    _add(store, "bug", "Checkout crash", ["part_of=sprint-4"])
    context = _context(store)
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    assert [phase["id"] for phase in value["phases"]] == ["sprint-4"]
    assert value["phases"][0]["container"] is True
    assert value["phases"][0]["state"] == "planned"
    tasks = {task["id"]: task for task in value["tasks"]}
    assert tasks["wire-checkout"]["workflow"] == "sprint"
    assert tasks["checkout-crash"]["workflow"] == "bugfix"
    assert tasks["wire-checkout"]["phase"] == "sprint-4"
    assert tasks["wire-checkout"]["states"] == ["todo", "ready", "doing", "dropped"]
    assert tasks["checkout-crash"]["states"] == ["reported", "fixing", "closed"]
    assert tasks["wire-checkout"]["etag"]


def test_a_priority_item_is_scope_and_not_a_roadmap_container(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    _add(store, "scope", "Checkout is safe", priority="must")
    context = _context(store)
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    assert {item["id"] for item in value["scope_items"]} == {"checkout-is-safe"}
    assert value["phases"] == []


def test_the_cursor_and_counts_follow_categories(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")
    _add(store, "sprint", "Sprint 1")
    shown = invoke("show", {"slug": "sprint-1"}, store=store)
    invoke(
        "move",
        {"slug": "sprint-1", "status": "doing", "expect": shown["value"]["doc"]["etag"]},
        store=store,
        actor="cto",
    )
    _add(store, "task", "Started")
    shown = invoke("show", {"slug": "started"}, store=store)
    invoke(
        "move",
        {"slug": "started", "status": "doing", "expect": shown["value"]["doc"]["etag"]},
        store=store,
        actor="cto",
    )
    _add(store, "task", "Waiting")
    context = _context(store)
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    assert value["counts"]["active"] == 1
    assert value["counts"]["not_started"] == 1
    assert value["project"]["status_summary"] == "1 not started · 1 in motion"
    assert "Started" in value["cursor"]["in_progress"]
    assert "Waiting" in value["cursor"]["next"]


def test_the_snapshot_carries_actors_skills_and_charter(tmp_path: Path) -> None:
    store = init_store(tmp_path, "basic")
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("basic", store),
        actor="cto",
    )
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    assert isinstance(value["actors"], list)
    assert isinstance(value["skills"], list)
    assert value["charter"] is None or isinstance(value["charter"], dict)
    for doc in value["actors"]:
        assert doc["primitive"] == "actor"
        assert "fields" in doc and "etag" in doc
    for doc in value["skills"]:
        assert doc["preset"] == "skill"
