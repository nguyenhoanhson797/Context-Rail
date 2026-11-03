"""A document write lands on the path the resolver derives from the graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from context_rail.gateway.gateway import invoke
from helpers import write_pack


def _pack() -> dict[str, Any]:
    return {
        "name": "nested",
        "version": 1,
        "statuses": {"todo": "not_started", "done": "done"},
        "initial": "todo",
        "transitions": {"todo": ["done"], "done": []},
        "presets": {
            "phase": {"primitive": "item", "tags": ["phase"]},
            "sprint": {"primitive": "item", "tags": ["sprint"]},
            "task": {
                "primitive": "item",
                "tags": ["task"],
                "required": ["area"],
                "defaults": {"area": "general"},
                "fields": {"area": ["frontend", "backend", "general"]},
            },
            "result": {"primitive": "record", "tags": ["result"]},
            "decision": {"primitive": "record", "tags": ["decision"]},
            "event": {"primitive": "record", "tags": ["event"]},
            "note": {"primitive": "note", "tags": []},
            "check": {"primitive": "check", "tags": []},
            "charter": {"primitive": "policy", "tags": []},
            "goal": {"primitive": "item", "tags": ["goal"]},
            "scope": {"primitive": "item", "tags": ["scope"]},
            "vision": {"primitive": "note", "tags": []},
            "wont": {"primitive": "note", "tags": []},
            "skill": {"primitive": "note", "tags": []},
            "cto": {"primitive": "actor", "tags": []},
            "profile": {"primitive": "actor", "tags": []},
        },
        "policy": {"stuck_hours": 24, "retries": 1},
    }


def _store(tmp_path: Path) -> Path:
    store = tmp_path / "context-rail"
    write_pack(store, "nested", _pack())
    templates = store / "packs" / "nested" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    from importlib import resources

    for name in ("item.md", "record.md"):
        source = resources.files("context_rail").joinpath("packs/basic/templates", name)
        templates.joinpath(name).write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    result = invoke(
        "init",
        {"dir": str(tmp_path), "pack": "nested", "scaffold": True},
        actor="human",
    )
    assert result["ok"] is True, result
    return Path(result["value"]["store"])


def _manifest(store: Path) -> None:
    path = store / "rail.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["presets"].update(
        {
            "phase": "items/{preset}/{slug}.md",
            "sprint": "{part_of}/{slug}.md",
            "task": "{part_of}/{field:area}/{slug}.md",
            "result": "records/{about}/{id}.json",
        }
    )
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _add(store: Path, preset: str, title: str, rel: list[str] | None = None,
         values: list[str] | None = None) -> Any:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or [], "set": values or []},
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def test_add_and_record_use_the_resolver(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _manifest(store)
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
