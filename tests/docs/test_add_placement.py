"""A document write lands on the path the resolver derives from the graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.gateway.gateway import invoke
from helpers import init_nested_store, set_preset_placement


def _add(
    store: Path,
    preset: str,
    title: str,
    rel: list[str] | None = None,
    values: list[str] | None = None,
) -> Any:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or [], "set": values or []},
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def test_add_and_record_use_the_resolver(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    phase = _add(store, "phase", "Phase 1")
    assert phase["path"] == "items/phase/phase-1.md"
    sprint = _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    assert sprint["path"] == "items/phase/phase-1/sprint-3.md"
    task = _add(
        store, "task", "Implement checkout", ["part_of=sprint-3"], ["area=frontend"]
    )
    assert task["path"] == (
        "items/phase/phase-1/sprint-3/frontend/implement-checkout.md"
    )
    result = _add(store, "result", "UI", ["about=implement-checkout"])
    assert result["path"] == (
        "records/items/phase/phase-1/sprint-3/frontend/implement-checkout/"
        f"{result['record']}.json"
    )

    updated = invoke(
        "update",
        {
            "slug": "implement-checkout",
            "set": ["area=backend"],
            "expect": task["etag"],
        },
        store=store,
        actor="cto",
    )
    assert updated["ok"] is True, updated
    assert updated["value"]["path"] == (
        "items/phase/phase-1/sprint-3/backend/implement-checkout.md"
    )
    assert not (store / task["path"]).exists()
    moved_record = store / (
        "records/items/phase/phase-1/sprint-3/backend/implement-checkout/"
        f"{result['record']}.json"
    )
    assert moved_record.is_file()


def test_a_decision_record_uses_its_own_about_placement(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    set_preset_placement(store, decision="records/{about}/{id}.json")
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Task", ["part_of=sprint-3"])

    decision = _add(store, "decision", "Adopt it", ["about=task"])

    assert decision["path"] == (
        "records/items/phase/phase-1/sprint-3/general/task/"
        f"{decision['record']}.json"
    )


def test_relocate_moves_a_record_without_rewriting_it(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Task", ["part_of=sprint-3"])
    result = _add(store, "result", "UI", ["about=task"])
    before = (store / result["path"]).read_bytes()

    set_preset_placement(store, result="records/archive/{id}.json")

    moved = invoke("relocate", {"key": "one"}, store=store, actor="cto")

    assert moved["ok"] is True, moved
    assert any(item["from"] == result["path"] for item in moved["value"]["moves"])
    target = f"records/archive/{result['record']}.json"
    assert (store / target).read_bytes() == before


def test_rename_moves_a_structural_document_and_rewrites_its_links(
    tmp_path: Path,
) -> None:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    task = _add(store, "task", "Task", ["part_of=sprint-3"])
    child = _add(store, "task", "Child", ["part_of=task"])
    note = _add(store, "note", "About child", ["about=child"])
    result = _add(store, "result", "UI", ["about=child"])

    renamed = invoke(
        "rename", {"old": "child", "new": "renamed-child"}, store=store, actor="cto"
    )

    assert renamed["ok"] is True, renamed
    target = Path(child["path"]).with_name("renamed-child.md").as_posix()
    assert renamed["value"]["path"] == target
    assert not (store / child["path"]).exists()
    assert "[[renamed-child]]" in (store / note["path"]).read_text(encoding="utf-8")
    assert (store / result["path"]).is_file()
    assert renamed["value"]["rewritten"] >= 1
