"""A nested pack through placement, metadata, rename and the migration guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag
from helpers import init_nested_store, nested_pack_document, write_pack


def _add(
    store: Path,
    preset: str,
    title: str,
    rel: list[str] | None = None,
    values: list[str] | None = None,
) -> dict[str, Any]:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or [], "set": values or []},
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def _codes(store: Path) -> list[str]:
    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"] is True, report
    return [issue["code"] for issue in report["value"]["issues"]]


def _structural_pack(directory: Path, name: str) -> Path:
    store = directory / "context-rail"
    write_pack(store, name, nested_pack_document(name))
    (store / "packs" / name / "layout.yaml").write_text(
        "placement:\n"
        '  record: "records/{yyyy}-{mm}/{id}.json"\n'
        "presets:\n"
        '  sprint: "{part_of}/{slug}.md"\n',
        encoding="utf-8",
        newline="\n",
    )
    return store


def test_a_nested_pack_survives_placement_metadata_rename_and_the_guards(
    tmp_path: Path, legacy_v2_store: Path
) -> None:
    store = init_nested_store(tmp_path)

    phase = _add(store, "phase", "Phase 1")
    sprint = _add(store, "sprint", "Sprint 3", [f"part_of={phase['slug']}"])
    task = _add(
        store,
        "task",
        "Implement checkout",
        [f"part_of={sprint['slug']}"],
        ["area=frontend"],
    )
    result = _add(store, "result", "Screenshots", [f"about={task['slug']}"])
    note = _add(store, "note", "About the task", [f"about={task['slug']}"])

    assert phase["path"] == "items/phase/phase-1.md"
    assert sprint["path"] == "items/phase/phase-1/sprint-3.md"
    assert task["path"] == (
        "items/phase/phase-1/sprint-3/frontend/implement-checkout.md"
    )
    assert result["path"].startswith(
        "records/items/phase/phase-1/sprint-3/frontend/implement-checkout/"
    )
    assert _codes(store) == []

    before_record = (store / result["path"]).read_bytes()
    moved = invoke(
        "update",
        {
            "slug": "implement-checkout",
            "set": ["area=backend"],
            "expect": task["etag"],
        },
        store=store,
        actor="cto",
    )
    assert moved["ok"] is True, moved
    task_path = "items/phase/phase-1/sprint-3/backend/implement-checkout.md"
    record_path = (
        "records/items/phase/phase-1/sprint-3/backend/implement-checkout/"
        f"{result['record']}.json"
    )
    assert moved["value"]["path"] == task_path
    assert not (store / task["path"]).exists()
    assert (store / record_path).is_file()
    assert (store / record_path).read_bytes() == before_record
    assert _codes(store) == []

    dry = invoke("repath", {"dry_run": True}, store=store, actor="cto")
    assert dry["ok"] is True, dry
    assert dry["value"]["moves"] == []

    renamed = invoke(
        "rename",
        {"old": "implement-checkout", "new": "ship-checkout"},
        store=store,
        actor="cto",
    )
    assert renamed["ok"] is True, renamed
    assert renamed["value"]["path"] == (
        "items/phase/phase-1/sprint-3/backend/ship-checkout.md"
    )
    assert not (store / task_path).exists()
    assert "[[ship-checkout]]" in (store / note["path"]).read_text(encoding="utf-8")
    assert "placement_historical" in _codes(store)

    target = _structural_pack(tmp_path / "migration", "struct")
    migrated = invoke(
        "migrate",
        {
            "source": str(legacy_v2_store),
            "dir": str(target.parent),
            "pack": "struct",
            "key": "structural",
        },
        actor="human",
    )
    assert migrated["ok"] is False
    assert migrated["error"]["code"] == "VALIDATION_FAILED"
    assert "graph" in migrated["error"]["message"]
    assert not (target / "rail.yaml").exists()

    strict = nested_pack_document("strict")
    strict["presets"]["task"]["required"] = ["area", "component"]
    strict["presets"]["task"]["defaults"] = {"area": "general"}
    write_pack(store, "strict", strict)
    switched = invoke(
        "pack use",
        {
            "name": "strict",
            "expect": etag((store / "rail.yaml").read_bytes()),
            "confirm": "USE PACK strict",
        },
        store=store,
        actor="human",
    )
    assert switched["ok"] is False
    assert switched["error"]["code"] == "VALIDATION_FAILED"
    assert "component" in str(switched["error"]["details"])
