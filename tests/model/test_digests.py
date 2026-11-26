"""Semantic, presentation and full digests are distinct and deterministic."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.compiler import compile_project, semantic_digest
from context_rail.model.pack import load_pack
from context_rail.model.presentation import default_presentation, load_presentation
from context_rail.store.index import pack_hash
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
    "policy": {"stuck_hours": 24},
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


#: Pinned semantic digests for shipped packs including their declared result contracts.
_SHIPPED_DIGESTS = {
    "basic": "ea82ae4054c089f1e19d5e2df1b73e1778cf26f91b7b2860bd00ebf862a8f011",
    "flow": "5eccecb3dcce8ac2800c97b366b32a41c368c41ac6456c581d54d560d9be3b58",
    "scrum": "5986641260bdedbfc7ef3369e9097e92d5fed931ef5336f290e77ec1d4c1a8cb",
    "video": "65f5e196067b3473e17c3ef2461fba55c810244847a6ba676fdb85bbcbf3484c",
    "waterfall": "a9556d7ed9b58f7eaa1c355fcba5a30cc2d6c7a1045b93a11d0caa472f6d9588",
}

_LEAD: dict[str, Any] = {
    "lead": {
        "anchor": "project",
        "reach": {"read": ["project"], "write": ["project"]},
        "can": {"move": {"shot": ["active"]}},
    }
}


def _roles_document(roles: dict[str, Any] | None) -> dict[str, Any]:
    document = copy.deepcopy(_DOCUMENT)
    document["operating"]["version"] = 2
    if roles is not None:
        document["operating"]["roles"] = roles
    return document


@pytest.mark.parametrize("name", sorted(_SHIPPED_DIGESTS))
def test_a_shipped_pack_digest_is_unchanged_by_roles(name: str, tmp_path: Path) -> None:
    pack = load_pack(name, tmp_path)

    assert semantic_digest(pack) == _SHIPPED_DIGESTS[name]
    assert pack_hash(pack) == _SHIPPED_DIGESTS[name]


def test_adding_a_role_changes_the_semantic_digest(tmp_path: Path) -> None:
    before = _model(tmp_path, _roles_document(None))
    after = _model(tmp_path, _roles_document(_LEAD))

    assert before.semantic_digest != after.semantic_digest
    assert before.full_digest != after.full_digest


def test_widening_a_role_changes_the_semantic_digest(tmp_path: Path) -> None:
    narrow = _model(tmp_path, _roles_document(_LEAD))
    wide = copy.deepcopy(_LEAD)
    wide["lead"]["can"]["move"]["shot"] = ["active", "done"]

    assert narrow.semantic_digest != _model(tmp_path, _roles_document(wide)).semantic_digest


def test_an_empty_roles_block_keeps_the_digest(tmp_path: Path) -> None:
    without = _model(tmp_path, _roles_document(None))

    assert without.semantic_digest == _model(tmp_path, _roles_document({})).semantic_digest


def test_a_label_change_keeps_the_semantic_digest_of_a_role_pack(tmp_path: Path) -> None:
    _write_surface(tmp_path, _SURFACE)
    before = _model(tmp_path, _roles_document(_LEAD))
    changed = copy.deepcopy(_SURFACE)
    changed["labels"]["shot"] = {"one": "Take", "many": "Takes"}
    _write_surface(tmp_path, changed)
    after = _model(tmp_path, _roles_document(_LEAD))

    assert before.semantic_digest == after.semantic_digest
    assert before.presentation_digest != after.presentation_digest


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


def test_result_contract_changes_semantic_digest_only(tmp_path: Path) -> None:
    from context_rail.model.compiler import load_project
    from helpers import init_result_store

    store = init_result_store(tmp_path)
    before = load_project("result-test", store)

    surface_path = store / "packs" / "result-test" / "surface.yaml"
    surface_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "labels": {"task": {"one": "Item", "many": "Items"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    after_surface = load_project("result-test", store)
    assert after_surface.semantic_digest == before.semantic_digest
    assert after_surface.presentation_digest != before.presentation_digest

    pack_path = store / "packs" / "result-test" / "pack.yaml"
    doc = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    doc["results"]["produce"]["sections"] = ["Summary", "Evidence"]
    pack_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")
    after_pack = load_project("result-test", store)
    assert after_pack.semantic_digest != before.semantic_digest
    assert after_pack.results["sprint"].produce.digest != before.results["sprint"].produce.digest
