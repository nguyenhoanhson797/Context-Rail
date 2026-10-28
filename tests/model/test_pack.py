"""pack.yaml validation, presets and the built-in small pack (fail closed)."""

from __future__ import annotations

import copy
import importlib.resources
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.pack import ModelError, Pack, Preset, load_pack

BASE_PACK: dict[str, Any] = {
    "name": "small",
    "version": 1,
    "statuses": {
        "todo": "not_started",
        "doing": "active",
        "blocked": "blocked",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "todo",
    "transitions": {
        "todo": ["doing", "dropped"],
        "doing": ["blocked", "done", "dropped", "todo"],
        "blocked": ["doing", "dropped"],
        "done": ["doing"],
        "dropped": ["todo"],
    },
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"]},
        "task": {"primitive": "item", "tags": ["task"]},
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": ["decision"]},
        "check": {"primitive": "check", "tags": []},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
}

_DELETE = object()


def _write_pack(store: Path, payload: dict[str, Any]) -> Path:
    path = store / "packs" / "small" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _pack(*mutations: tuple[str, object]) -> dict[str, Any]:
    payload = copy.deepcopy(BASE_PACK)
    for path, value in mutations:
        keys = path.split(".")
        target: Any = payload
        for key in keys[:-1]:
            target = target[key]
        if value is _DELETE:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return payload


def test_small_pack_loads_and_maps_categories(tmp_path: Path) -> None:
    p = load_pack("small", tmp_path)
    assert isinstance(p, Pack)
    assert p.statuses["doing"] == "active" and p.initial == "todo"
    assert p.presets["risk"].fields["severity"] == ("low", "medium", "high", "critical")


def test_builtin_small_pack_matches_pinned_values(tmp_path: Path) -> None:
    p = load_pack("small", tmp_path)
    assert p.name == "small"
    assert p.version == 1
    assert p.statuses == {
        "todo": "not_started",
        "doing": "active",
        "blocked": "blocked",
        "done": "done",
        "dropped": "dropped",
    }
    assert p.initial == "todo"
    assert p.transitions == {
        "todo": ("doing", "dropped"),
        "doing": ("blocked", "done", "dropped", "todo"),
        "blocked": ("doing", "dropped"),
        "done": ("doing",),
        "dropped": ("todo",),
    }
    assert p.policy == {"stuck_hours": 24, "retries": 1}
    expected = {
        "goal": ("item", ("goal",), {}),
        "scope": (
            "item",
            ("scope",),
            {"priority": ("must", "should", "could", "wont")},
        ),
        "task": ("item", ("task",), {}),
        "milestone": ("item", ("milestone",), {}),
        "risk": (
            "item",
            ("risk",),
            {"severity": ("low", "medium", "high", "critical")},
        ),
        "question": ("item", ("question",), {}),
        "note": ("note", (), {}),
        "convention": ("note", ("convention",), {}),
        "decision": ("record", ("decision",), {}),
        "event": ("record", ("event",), {}),
        "check": ("check", (), {}),
    }
    assert set(p.presets) == set(expected)
    for name, (primitive, tags, fields) in expected.items():
        preset = p.presets[name]
        assert isinstance(preset, Preset)
        assert preset.name == name
        assert preset.primitive == primitive
        assert preset.tags == tags
        assert dict(preset.fields) == fields


def test_default_template_follows_the_primitive(tmp_path: Path) -> None:
    p = load_pack("small", tmp_path)
    assert p.presets["task"].template == "templates/item.md"
    assert p.presets["note"].template == "templates/note.md"
    assert p.presets["decision"].template == "templates/record.md"
    assert p.presets["check"].template == "templates/check.md"


def test_builtin_pack_ships_body_templates() -> None:
    templates = importlib.resources.files("context_rail").joinpath(
        "packs", "small", "templates"
    )
    assert sorted(entry.name for entry in templates.iterdir()) == [
        "check.md",
        "item.md",
        "note.md",
        "record.md",
    ]


def test_local_pack_overrides_builtin(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("initial", "done")))
    assert load_pack("small", tmp_path).initial == "done"


def test_local_pack_template_override_is_kept(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("presets.task.template", "templates/task.md")))
    assert load_pack("small", tmp_path).presets["task"].template == "templates/task.md"


@pytest.mark.parametrize(
    "mutations",
    [
        pytest.param((("name", "other"),), id="name-mismatch"),
        pytest.param((("name", "../small"),), id="name-escape"),
        pytest.param((("color", "red"),), id="extra-top-level-key"),
        pytest.param((("statuses", _DELETE),), id="missing-statuses"),
        pytest.param((("version", 2),), id="unsupported-version"),
        pytest.param((("version", "1"),), id="non-integer-version"),
        pytest.param((("statuses.doing", "finished"),), id="unknown-category"),
        pytest.param((("statuses.doing", 1),), id="non-string-category"),
        pytest.param((("initial", "nope"),), id="unknown-initial"),
        pytest.param((("transitions.todo", ["nope"]),), id="unknown-transition-target"),
        pytest.param((("transitions.todo", "doing"),), id="scalar-transition"),
        pytest.param((("transitions.nope", ["doing"]),), id="undeclared-transition-source"),
        pytest.param((("presets.task.color", "red"),), id="preset-extra-key"),
        pytest.param((("presets.task.primitive", "widget"),), id="preset-unknown-primitive"),
        pytest.param((("presets.task.tags", "task"),), id="preset-tags-scalar"),
        pytest.param((("presets.task.tags", [1]),), id="preset-tags-non-string"),
        pytest.param(
            (("presets.task.fields", {"priority": "must"}),), id="preset-fields-scalar"
        ),
        pytest.param(
            (("presets.task.fields", {"priority": [1]}),), id="preset-field-enum-non-string"
        ),
        pytest.param((("policy.mood", "x"),), id="unknown-policy-key"),
        pytest.param((("policy.stuck_hours", _DELETE),), id="missing-stuck-hours"),
        pytest.param((("policy.retries", _DELETE),), id="missing-retries"),
        pytest.param((("policy.stuck_hours", True),), id="stuck-hours-bool"),
        pytest.param((("policy.stuck_hours", -1),), id="stuck-hours-negative"),
        pytest.param((("policy.stuck_hours", float("nan")),), id="stuck-hours-nan"),
        pytest.param((("policy.stuck_hours", float("inf")),), id="stuck-hours-inf"),
        pytest.param((("policy.stuck_hours", "24"),), id="stuck-hours-string"),
        pytest.param((("policy.retries", 1.5),), id="retries-float"),
        pytest.param((("policy.retries", -1),), id="retries-negative"),
        pytest.param((("policy.retries", True),), id="retries-bool"),
        pytest.param((("policy", [("stuck_hours", 24)]),), id="policy-list"),
    ],
)
def test_invalid_local_pack_is_refused(
    tmp_path: Path, mutations: tuple[tuple[str, object], ...]
) -> None:
    _write_pack(tmp_path, _pack(*mutations))
    with pytest.raises(ModelError) as err:
        load_pack("small", tmp_path)
    if mutations == (("version", 2),):
        assert err.value.code in ("SCHEMA_UNSUPPORTED", "VALIDATION_FAILED")
    else:
        assert err.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(("key", "value"), [("stuck_hours", 0.5), ("retries", 0)])
def test_policy_values_are_accepted_and_kept(
    tmp_path: Path, key: str, value: float | int
) -> None:
    _write_pack(tmp_path, _pack((f"policy.{key}", value)))
    assert load_pack("small", tmp_path).policy[key] == value


@pytest.mark.parametrize("name", ["../small", "a/b", "C:small", "..", "a\\b", ""])
def test_pack_name_escapes_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ModelError) as err:
        load_pack(name, tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_unknown_pack_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelError) as err:
        load_pack("indie", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_broken_local_pack_does_not_fall_back_to_builtin(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("initial", "nope")))
    with pytest.raises(ModelError) as err:
        load_pack("small", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_pack_cache_tracks_path_mtime_size_and_override_presence(
    tmp_path: Path,
) -> None:
    assert load_pack("small", tmp_path).initial == "todo"

    path = _write_pack(tmp_path, _pack(("initial", "done")))
    assert load_pack("small", tmp_path).initial == "done"

    size_with_done = path.stat().st_size
    _write_pack(tmp_path, _pack(("initial", "todo")))
    assert path.stat().st_size == size_with_done
    stale = path.stat()
    os.utime(path, ns=(stale.st_atime_ns, stale.st_mtime_ns + 2_000_000_000))
    assert load_pack("small", tmp_path).initial == "todo"

    path.unlink()
    assert load_pack("small", tmp_path).initial == "todo"
