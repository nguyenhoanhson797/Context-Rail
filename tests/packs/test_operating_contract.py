"""Pack tools compile the operating model and report semantic digests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from helpers import copy_pack_templates, init_store, write_pack

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
        "shot": {"primitive": "item", "tags": ["shot"], "container": False},
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

_SURFACE = {
    "version": 1,
    "labels": {"shot": {"one": "Shot", "many": "Shots"}},
    "screens": [{"id": "board", "kind": "board", "title": "Shots"}],
    "fields": {"card": ["status"], "filters": ["status"], "columns": ["title"]},
}


def _write_surface(store: Path, payload: dict[str, Any], *, name: str = "studio") -> Path:
    path = store / "packs" / name / "surface.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return path


def _store_with_studio(tmp_path: Path) -> Path:
    store = init_store(tmp_path)
    write_pack(store, "studio", _DOCUMENT)
    copy_pack_templates(store, "studio")
    _write_surface(store, _SURFACE)
    return store


def test_pack_show_reports_operating_and_three_digests(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)

    shown = invoke("pack show", {"name": "studio"}, store=store, actor="human")

    assert shown["ok"] is True, shown
    value = shown["value"]
    assert value["operating"]["unit"] == "shot"
    assert value["semantic_digest"]
    assert value["presentation_digest"]
    assert value["full_digest"]
    assert value["approval"]["state"] in ("approved", "mismatch", "missing")


def test_pack_show_reports_a_neutral_model(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    shown = invoke("pack show", {"name": "basic"}, store=store, actor="human")

    assert shown["value"]["operating"] == "neutral"
    assert shown["value"]["semantic_digest"]


def test_a_label_change_keeps_the_semantic_digest(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)
    before = invoke("pack show", {"name": "studio"}, store=store, actor="human")["value"]

    changed = copy.deepcopy(_SURFACE)
    changed["labels"]["shot"] = {"one": "Take", "many": "Takes"}
    _write_surface(store, changed)
    after = invoke("pack show", {"name": "studio"}, store=store, actor="human")["value"]

    assert before["semantic_digest"] == after["semantic_digest"]
    assert before["presentation_digest"] != after["presentation_digest"]
    assert before["full_digest"] != after["full_digest"]


def test_pack_validate_reports_an_invalid_operating_block(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = copy.deepcopy(_DOCUMENT)
    document["operating"]["focus"]["preset"] = "shot"
    write_pack(store, "studio", document)

    result = invoke("pack validate", {"name": "studio"}, store=store, actor="cto")

    assert result["value"]["valid"] is False
    problem = result["value"]["problems"][0]
    assert problem["path"] == "pack.yaml"
    assert "hierarchy" in problem["message"]
    assert problem["fix"].startswith("fix pack.yaml")


def test_pack_validate_reports_an_invalid_surface(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)
    _write_surface(
        store,
        {**_SURFACE, "labels": {"ghost": {"one": "Ghost", "many": "Ghosts"}}},
    )

    result = invoke("pack validate", {"name": "studio"}, store=store, actor="cto")

    assert result["value"]["valid"] is False
    assert [problem["path"] for problem in result["value"]["problems"]] == [
        "surface.yaml"
    ]


def test_pack_validate_accepts_a_declared_operating_model(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)

    result = invoke("pack validate", {"name": "studio"}, store=store, actor="cto")

    assert result["value"] == {"valid": True, "problems": []}


def test_pack_create_copies_the_surface_file(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)

    result = invoke(
        "pack create",
        {"name": "studio-copy", "from": "studio"},
        store=store,
        actor="human",
    )

    assert result["ok"] is True, result
    assert (store / "packs" / "studio-copy" / "surface.yaml").is_file()


def test_pack_create_refuses_an_invalid_surface(tmp_path: Path) -> None:
    store = _store_with_studio(tmp_path)
    _write_surface(
        store,
        {**_SURFACE, "screens": [{"id": "s", "kind": "gallery", "title": "S"}]},
    )

    result = invoke(
        "pack create",
        {"name": "studio-copy", "from": "studio"},
        store=store,
        actor="human",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "packs" / "studio-copy").exists()
