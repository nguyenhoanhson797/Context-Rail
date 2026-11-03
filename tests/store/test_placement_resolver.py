"""The placement resolver: graph-driven paths, fallbacks and refusals."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import DEFAULT_MANIFEST, manifest_from_mapping
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from context_rail.store.placement import (
    PendingDocument,
    PlacementContext,
    PlacementError,
    PlacementRequest,
    expected_path,
    expected_paths,
)
from helpers import init_store, write_pack


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


def _manifest(store: Path, extra: dict[str, str] | None = None) -> Any:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["pack"] = "nested"
    payload["presets"].update(
        {
            "phase": "items/{preset}/{slug}.md",
            "sprint": "{part_of}/{slug}.md",
            "task": "{part_of}/{field:area}/{slug}.md",
            "result": "records/{about}/{id}.json",
            "decision": "records/decisions/{slug}.md",
        }
    )
    if extra:
        payload["presets"].update(extra)
    return manifest_from_mapping(payload, store)


def _add(
    store: Path,
    preset: str,
    title: str,
    rel: list[str] | None = None,
    set_values: list[str] | None = None,
) -> Any:
    result = invoke(
        "add",
        {
            "preset": preset,
            "title": title,
            "rel": rel or [],
            "set": set_values or [],
        },
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def _init_nested(tmp_path: Path) -> Path:
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


def _graph(store: Path) -> tuple[Any, Any, Index]:
    pack = load_pack("nested", store)
    manifest = _manifest(store)
    index = Index.open(store, pack)
    index.sync()
    return manifest, pack, index


def test_resolves_a_nested_tree_and_a_related_record(tmp_path: Path) -> None:
    store = _init_nested(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Implement checkout", ["part_of=sprint-3"], ["area=frontend"])
    result = _add(store, "result", "UI screenshots", ["about=implement-checkout"])
    manifest, pack, index = _graph(store)
    try:
        found, errors = expected_paths(index, manifest, pack)
    finally:
        index.close()

    assert errors == []
    assert found["phase-1"] == "items/phase/phase-1.md"
    assert found["sprint-3"] == "items/phase/phase-1/sprint-3.md"
    assert (
        found["implement-checkout"]
        == "items/phase/phase-1/sprint-3/frontend/implement-checkout.md"
    )
    assert found[result["slug"]] == (
        "records/items/phase/phase-1/sprint-3/frontend/implement-checkout/"
        f"{result['record'] or result['slug']}.json"
    )


def test_simple_template_matches_render_path() -> None:
    manifest = manifest_from_mapping(copy.deepcopy(DEFAULT_MANIFEST))
    context = PlacementContext(manifest=manifest, pack=load_pack("basic", Path(".")))
    request = PlacementRequest(
        primitive="item", preset="task", slug="login", created="2026-10-28T00:00:00Z"
    )
    assert expected_path(context, request) == "items/task/login.md"


def test_field_default_and_missing_field(tmp_path: Path) -> None:
    store = _init_nested(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Defaulted", ["part_of=sprint-3"])
    manifest, pack, index = _graph(store)
    try:
        found, errors = expected_paths(index, manifest, pack)
    finally:
        index.close()
    assert errors == []
    assert found["defaulted"].endswith("/general/defaulted.md")


def test_missing_anchor_is_historical_for_a_record(tmp_path: Path) -> None:
    store = _init_nested(tmp_path)
    _add(store, "result", "Orphan")
    manifest, pack, index = _graph(store)
    try:
        found, errors = expected_paths(index, manifest, pack)
    finally:
        index.close()
    assert "orphan" not in found
    assert [error.code for error in errors] == ["placement_historical"]
    assert errors[0].historical is True


def test_multiple_anchors_are_ambiguous() -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"]["task"] = "{part_of}/{slug}.md"
    manifest = manifest_from_mapping(payload)
    context = PlacementContext(manifest=manifest, pack=load_pack("basic", Path(".")))
    request = PlacementRequest(
        primitive="item",
        preset="task",
        slug="x",
        relations={"part_of": ("a", "b")},
    )
    with pytest.raises(PlacementError) as error:
        expected_path(context, request)
    assert error.value.code == "placement_ambiguous"


def test_kind_placement_wins_over_the_record_preset(tmp_path: Path) -> None:
    store = _init_nested(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Task", ["part_of=sprint-3"])
    result = _add(store, "result", "UI", ["about=task"])
    pack = load_pack("nested", store)
    manifest = _manifest(store, {"kind:result": "records/{kind}/{id}.json"})
    index = Index.open(store, pack)
    index.sync()
    try:
        found, errors = expected_paths(index, manifest, pack)
    finally:
        index.close()
    assert errors == []
    assert found[result["slug"]] == f"records/result/{result['record']}.json"


def test_pending_parent_and_verifies_token() -> None:
    manifest = manifest_from_mapping(
        copy.deepcopy(DEFAULT_MANIFEST)
        | {"presets": {**DEFAULT_MANIFEST["presets"], "task": "{part_of}/{slug}.md"}}
    )
    context = PlacementContext(
        manifest=manifest,
        pack=load_pack("basic", Path(".")),
        pending=(
            PendingDocument(
                slug="parent",
                primitive="item",
                preset="task",
                path="items/task/parent.md",
            ),
        ),
    )
    request = PlacementRequest(
        primitive="item",
        preset="task",
        slug="child",
        relations={"part_of": ("parent",)},
    )
    assert expected_path(context, request) == "items/task/parent/child.md"


def test_part_of_cycle_is_reported(tmp_path: Path) -> None:
    store = _init_nested(tmp_path)
    first = _add(store, "task", "First")
    second = _add(store, "task", "Second")
    linked = invoke(
        "link",
        {
            "src": "first",
            "rel": "part_of",
            "dst": "second",
            "expect": first["etag"],
        },
        store=store,
        actor="cto",
    )
    assert linked["ok"] is True, linked
    linked = invoke(
        "link",
        {
            "src": "second",
            "rel": "part_of",
            "dst": "first",
            "expect": second["etag"],
        },
        store=store,
        actor="cto",
    )
    assert linked["ok"] is False
    assert linked["error"]["code"] == "CYCLE_DETECTED"
