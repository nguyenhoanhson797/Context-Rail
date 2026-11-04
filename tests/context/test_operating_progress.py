"""Progress resolution: declared completion states, groups and categories."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.context.operating import resolve_progress
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.compiler import compile_project
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import copy_pack_templates, init_pack_store, init_store, write_pack


def _context(store: Path, pack: str) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor="cto",
    )


def _add(store: Path, preset: str, title: str, **extra: Any) -> str:
    result = invoke(
        "add", {"preset": preset, "title": title, **extra}, store=store, actor="cto"
    )
    assert is_ok(result), result
    return result["value"]["slug"]


def _move(store: Path, slug: str, status: str) -> None:
    etag = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]
    result = invoke(
        "move", {"slug": slug, "status": status, "expect": etag}, store=store, actor="cto"
    )
    assert is_ok(result), result


def test_scrum_progress_counts_story_completion(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    ctx = _context(store, "scrum")
    model = operating_scrum_store.model

    before = resolve_progress(ctx, model)
    _move(store, operating_scrum_store.story, "doing")
    _move(store, operating_scrum_store.story, "done")
    after = resolve_progress(ctx, model)

    assert before.total == 1 and before.complete == 0
    assert after.total == 1 and after.complete == 1
    assert after.by_category["done"] == 1
    assert after.children[0]["slug"] == operating_scrum_store.sprint


def test_a_dropped_item_never_counts_as_complete(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    dropped = _add(store, "story", "Dropped story", rel=[f"part_of={operating_scrum_store.sprint}"])
    _move(store, dropped, "dropped")
    ctx = _context(store, "scrum")

    view = resolve_progress(ctx, operating_scrum_store.model)

    assert view.total == 2
    assert view.complete == 0
    assert view.by_category["dropped"] == 1


def test_waterfall_groups_follow_the_declared_order(
    operating_waterfall_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_waterfall_store.store, "waterfall")

    view = resolve_progress(ctx, operating_waterfall_store.model)

    assert view.group == "phase"
    assert [child["slug"] for child in view.children] == [
        operating_waterfall_store.phase,
        operating_waterfall_store.next_phase,
    ]
    assert [child["total"] for child in view.children] == [1, 1]
    assert view.total == 2


def test_a_project_without_a_hierarchy_has_one_wide_group(tmp_path: Path) -> None:
    store = init_store(tmp_path / "plain")
    write_pack(
        store,
        "plain",
        {
            "name": "plain",
            "version": 1,
            "statuses": {"todo": "not_started", "done": "done", "dropped": "dropped"},
            "initial": "todo",
            "transitions": {"todo": ["done", "dropped"], "done": [], "dropped": []},
            "presets": {
                "task": {"primitive": "item", "tags": ["task"]},
                "note": {"primitive": "note", "tags": []},
            },
            "policy": {"stuck_hours": 24, "retries": 1},
        },
    )
    copy_pack_templates(store, "plain")
    _add(store, "task", "One")
    _add(store, "task", "Two")
    ctx = _context(store, "plain")
    pack = load_pack("plain", store)

    view = resolve_progress(ctx, compile_project(pack))

    assert view.group is None
    assert view.total == 2
    assert view.children == ()


def test_a_mixed_workflow_pack_uses_per_preset_states(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path / "mixed", _mixed_document())
    task = _add(store, "task", "A task")
    bug = _add(store, "bug", "A bug")
    _move(store, task, "done")
    _move(store, bug, "closed")
    ctx = _context(store, "mixed")
    pack = load_pack("mixed", store)
    model = compile_project(pack, pack.operating)

    view = resolve_progress(ctx, model)

    assert view.total == 2
    assert view.complete == 2
    assert view.by_category["done"] == 2


def _mixed_document() -> dict[str, Any]:
    return {
        "name": "mixed",
        "version": 2,
        "statuses": {
            "todo": "not_started",
            "doing": "active",
            "done": "done",
            "closed": "done",
            "dropped": "dropped",
        },
        "initial": "todo",
        "transitions": {
            "todo": ["doing", "done", "closed", "dropped"],
            "doing": ["done", "closed", "dropped"],
            "done": [],
            "closed": [],
            "dropped": [],
        },
        "workflows": {
            "a": {
                "initial": "todo",
                "transitions": {
                    "todo": ["doing", "done"],
                    "doing": ["done"],
                    "done": [],
                },
            },
            "b": {
                "initial": "todo",
                "transitions": {
                    "todo": ["doing", "closed"],
                    "doing": ["closed"],
                    "closed": [],
                },
            },
        },
        "presets": {
            "goal": {"primitive": "item", "tags": ["goal"], "container": True},
            "scope": {"primitive": "item", "tags": ["scope"], "container": True},
            "task": {
                "primitive": "item",
                "tags": ["task"],
                "container": False,
                "workflow": "a",
            },
            "bug": {
                "primitive": "item",
                "tags": ["bug"],
                "container": False,
                "workflow": "b",
            },
            "note": {"primitive": "note", "tags": []},
            "decision": {"primitive": "record", "tags": ["decision"]},
            "event": {"primitive": "record", "tags": ["event"]},
            "check": {"primitive": "check", "tags": []},
            "charter": {"primitive": "policy", "tags": []},
            "vision": {"primitive": "note", "tags": []},
            "wont": {"primitive": "note", "tags": []},
            "skill": {"primitive": "note", "tags": []},
            "cto": {"primitive": "actor", "tags": []},
            "profile": {"primitive": "actor", "tags": []},
        },
        "policy": {"stuck_hours": 24, "retries": 1},
        "operating": {
            "version": 1,
            "unit": "task",
            "queue": {"presets": ["task", "bug"]},
            "progress": {
                "of": ["task", "bug"],
                "complete": {"presets": {"task": ["done"], "bug": ["closed"]}},
            },
        },
    }
