"""Semantic, presentation and full digests are distinct and deterministic."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.compiler import compile_project
from context_rail.model.pack import load_pack
from context_rail.model.presentation import default_presentation, load_presentation
from helpers import write_pack

_DOCUMENT: dict[str, Any] = {
    "name": "studio",
    "version": 2,
    "statuses": {
        "draft": "not_started",
        "active": "active",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "draft",
    "transitions": {
        "draft": ["active", "dropped"],
        "active": ["done", "dropped"],
        "done": ["active"],
        "dropped": ["draft"],
    },
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "scene": {"primitive": "item", "tags": ["scene"], "container": True},
        "shot": {
            "primitive": "item",
            "tags": ["shot"],
            "container": False,
            "fields": {"model": ["a", "b"]},
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
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
    "operating": {
        "version": 1,
        "unit": "shot",
        "hierarchy": [{"preset": "scene", "required": True}],
        "focus": {"preset": "scene", "category": "active", "resolve": "unique"},
        "queue": {"presets": ["shot"]},
        "progress": {"group": "scene", "of": ["shot"], "complete": {"category": "done"}},
    },
}

_SURFACE: dict[str, Any] = {
    "version": 1,
    "labels": {"shot": {"one": "Shot", "many": "Shots"}},
    "screens": [{"id": "board", "kind": "board", "title": "Shots"}],
    "fields": {"card": ["status", "model"], "filters": ["status"], "columns": ["title"]},
}


def _write_surface(store: Path, payload: dict[str, Any]) -> None:
    path = store / "packs" / "studio" / "surface.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _model(store: Path, document: dict[str, Any] | None = None) -> Any:
    write_pack(store, "studio", document or _DOCUMENT)
    pack = load_pack("studio", store)
    presentation = load_presentation("studio", store, pack)
    return compile_project(pack, None, presentation)


def test_a_label_only_change_changes_presentation_and_full_only(
    tmp_path: Path,
) -> None:
    _write_surface(tmp_path, _SURFACE)
    before = _model(tmp_path)
    changed = copy.deepcopy(_SURFACE)
    changed["labels"]["shot"] = {"one": "Take", "many": "Takes"}
    _write_surface(tmp_path, changed)
    after = _model(tmp_path)

    assert before.semantic_digest == after.semantic_digest
    assert before.presentation_digest != after.presentation_digest
    assert before.full_digest != after.full_digest


def test_a_card_only_change_changes_presentation_and_full_only(
    tmp_path: Path,
) -> None:
    _write_surface(tmp_path, _SURFACE)
    before = _model(tmp_path)
    changed = copy.deepcopy(_SURFACE)
    changed["fields"]["card"] = ["title", "status"]
    _write_surface(tmp_path, changed)
    after = _model(tmp_path)

    assert before.semantic_digest == after.semantic_digest
    assert before.presentation_digest != after.presentation_digest
    assert before.full_digest != after.full_digest


def test_an_operating_change_changes_the_semantic_digest(tmp_path: Path) -> None:
    _write_surface(tmp_path, _SURFACE)
    before = _model(tmp_path)
    document = copy.deepcopy(_DOCUMENT)
    document["operating"]["queue"]["presets"] = ["shot", "scene"]
    after = _model(tmp_path, document)

    assert before.semantic_digest != after.semantic_digest
    assert before.presentation_digest == after.presentation_digest
    assert before.full_digest != after.full_digest


def test_a_pack_workflow_change_changes_the_semantic_digest(
    tmp_path: Path,
) -> None:
    _write_surface(tmp_path, _SURFACE)
    before = _model(tmp_path)
    document = copy.deepcopy(_DOCUMENT)
    document["transitions"]["draft"] = ["active"]
    after = _model(tmp_path, document)

    assert before.semantic_digest != after.semantic_digest
    assert before.full_digest != after.full_digest


def test_a_placement_change_changes_the_semantic_digest(tmp_path: Path) -> None:
    _write_surface(tmp_path, _SURFACE)
    write_pack(tmp_path, "studio", _DOCUMENT)
    pack = load_pack("studio", tmp_path)
    presentation = load_presentation("studio", tmp_path, pack)

    first = compile_project(pack, None, presentation, placement={"shot": "a/{slug}.md"})
    second = compile_project(pack, None, presentation, placement={"shot": "b/{slug}.md"})

    assert first.semantic_digest != second.semantic_digest
    assert first.presentation_digest == second.presentation_digest


def test_repeated_loads_produce_the_same_digests(tmp_path: Path) -> None:
    _write_surface(tmp_path, _SURFACE)
    first = _model(tmp_path)
    second = _model(tmp_path)

    assert first.as_dict() == second.as_dict()
    assert first.to_json() == second.to_json()


def test_the_neutral_presentation_is_used_when_surface_is_absent(
    tmp_path: Path,
) -> None:
    write_pack(tmp_path, "studio", _DOCUMENT)
    pack = load_pack("studio", tmp_path)
    model = compile_project(pack, None, default_presentation(pack))

    assert model.presentation.labels["shot"].many == "Shots"
    assert model.presentation_digest == compile_project(pack).presentation_digest
