"""Preset metadata: required fields, defaults and the shared prepare helper."""

from __future__ import annotations

import copy
from pathlib import Path
from datetime import date
from typing import Any

import pytest
import yaml

from context_rail.model.errors import ModelError
from context_rail.model.manifest import DEFAULT_MANIFEST, manifest_from_mapping
from context_rail.model.metadata import ENGINE_PRESETS, prepare_fields
from context_rail.model.pack import load_pack

BASE: dict[str, Any] = {
    "name": "basic",
    "version": 1,
    "statuses": {"todo": "not_started", "done": "done"},
    "initial": "todo",
    "transitions": {"todo": ["done"], "done": []},
    "presets": {
        "task": {"primitive": "item", "tags": ["task"]},
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": []},
        "event": {"primitive": "record", "tags": []},
        "check": {"primitive": "check", "tags": []},
        "charter": {"primitive": "policy", "tags": []},
        "skill": {"primitive": "note", "tags": []},
        "cto": {"primitive": "actor", "tags": []},
        "profile": {"primitive": "actor", "tags": []},
        "goal": {"primitive": "item", "tags": ["goal"]},
        "scope": {"primitive": "item", "tags": ["scope"]},
        "vision": {"primitive": "note", "tags": []},
        "wont": {"primitive": "note", "tags": []},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
}


def _store(tmp_path: Path, preset: dict[str, Any]) -> Path:
    payload = copy.deepcopy(BASE)
    payload["presets"]["task"].update(preset)
    path = tmp_path / "packs" / "basic" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return tmp_path


def _pack(tmp_path: Path, preset: dict[str, Any]) -> Any:
    return load_pack("basic", _store(tmp_path, preset))


def _refused(tmp_path: Path, preset: dict[str, Any]) -> ModelError:
    with pytest.raises(ModelError) as error:
        _pack(tmp_path, preset)
    return error.value


def test_existing_pack_has_empty_metadata(tmp_path: Path) -> None:
    pack = _pack(tmp_path, {})
    assert pack.presets["task"].required == ()
    assert pack.presets["task"].defaults == {}


def test_required_and_defaults_parse(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        {"required": ["area", "component"], "defaults": {"area": "general"}},
    )
    preset = pack.presets["task"]
    assert preset.required == ("area", "component")
    assert preset.defaults == {"area": "general"}


def test_required_refuses_duplicates_empty_and_reserved(tmp_path: Path) -> None:
    assert "repeats" in _refused(tmp_path, {"required": ["area", "area"]}).message
    assert "non-empty" in _refused(tmp_path, {"required": [""]}).message
    for name in ("title", "status", "part_of", "slug"):
        assert "reserved" in _refused(tmp_path, {"required": [name]}).message
    assert "reserved" in _refused(
        tmp_path, {"primitive": "record", "required": ["kind"]}
    ).message


def test_required_refuses_a_malformed_name(tmp_path: Path) -> None:
    assert "malformed" in _refused(tmp_path, {"required": ["a/b"]}).message


def test_defaults_must_be_required_fields(tmp_path: Path) -> None:
    assert "not required" in _refused(
        tmp_path, {"required": ["area"], "defaults": {"component": "x"}}
    ).message


@pytest.mark.parametrize("value", [[], {}, None, date(2026, 1, 1)])
def test_defaults_refuse_non_scalars(tmp_path: Path, value: Any) -> None:
    assert "JSON scalar" in _refused(
        tmp_path, {"required": ["area"], "defaults": {"area": value}}
    ).message


def test_defaults_refuse_an_empty_string(tmp_path: Path) -> None:
    assert "empty" in _refused(
        tmp_path, {"required": ["area"], "defaults": {"area": ""}}
    ).message


def test_defaults_must_satisfy_the_enum(tmp_path: Path) -> None:
    assert "enum" in _refused(
        tmp_path,
        {
            "required": ["area"],
            "defaults": {"area": "nope"},
            "fields": {"area": ["frontend", "backend"]},
        },
    ).message


def test_prepare_merges_defaults_and_requires_the_rest(tmp_path: Path) -> None:
    preset = _pack(
        tmp_path,
        {"required": ["area", "component"], "defaults": {"area": "general"}},
    ).presets["task"]
    assert prepare_fields(preset, {"component": "checkout"}) == {
        "component": "checkout",
        "area": "general",
    }
    with pytest.raises(ModelError) as error:
        prepare_fields(preset, {})
    assert error.value.details["missing"] == ["component"]


def test_prepare_refuses_empty_values(tmp_path: Path) -> None:
    preset = _pack(tmp_path, {"required": ["area"]}).presets["task"]
    for value in ("", [], None):
        with pytest.raises(ModelError):
            prepare_fields(preset, {"area": value})


def test_prepare_runs_the_enum_and_engine_checks(tmp_path: Path) -> None:
    preset = _pack(
        tmp_path,
        {"required": ["area"], "fields": {"area": ["frontend", "backend"]}},
    ).presets["task"]
    with pytest.raises(ModelError) as error:
        prepare_fields(preset, {"area": "nope"})
    assert error.value.details["key"] == "area"
    check = _pack(tmp_path, {}).presets["check"]
    with pytest.raises(ModelError):
        prepare_fields(check, {"acceptance": "yes"})
    with pytest.raises(ModelError):
        prepare_fields(check, {"inputs": ["[z-a]"]})


def test_engine_presets_are_declared() -> None:
    assert "check" in ENGINE_PRESETS
    assert "task" not in ENGINE_PRESETS


def test_a_path_field_default_must_be_a_string(tmp_path: Path) -> None:
    store = _store(tmp_path, {"required": ["area"], "defaults": {"area": 3}})
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"]["task"] = "{field:area}/{slug}.md"
    with pytest.raises(ModelError) as error:
        manifest_from_mapping(payload, store)
    assert "string" in error.value.message
