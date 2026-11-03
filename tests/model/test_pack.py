"""pack.yaml validation, presets and the built-in basic pack (fail closed)."""

from __future__ import annotations

import copy
import importlib.resources
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.pack import (
    ModelError,
    Pack,
    Preset,
    QualityRule,
    Workflow,
    load_pack,
)

BASE_PACK: dict[str, Any] = {
    "name": "basic",
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
    path = store / "packs" / "basic" / "pack.yaml"
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


def test_basic_pack_loads_and_maps_categories(tmp_path: Path) -> None:
    p = load_pack("basic", tmp_path)
    assert isinstance(p, Pack)
    assert p.statuses["doing"] == "active" and p.initial == "todo"
    assert p.presets["risk"].fields["severity"] == ("low", "medium", "high", "critical")


def test_builtin_basic_pack_matches_pinned_values(tmp_path: Path) -> None:
    p = load_pack("basic", tmp_path)
    assert p.name == "basic"
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
        "bug": ("item", ("bug",), {}),
        "idea": ("item", ("idea",), {}),
        "milestone": ("item", ("milestone",), {}),
        "risk": (
            "item",
            ("risk",),
            {"severity": ("low", "medium", "high", "critical")},
        ),
        "question": ("item", ("question",), {}),
        "note": ("note", (), {}),
        "convention": ("note", ("convention",), {}),
        "code": (
            "note",
            ("code",),
            {"kind": ("file", "module", "class", "function", "method")},
        ),
        "decision": ("record", ("decision",), {}),
        "event": ("record", ("event",), {}),
        "check": ("check", (), {}),
        "charter": ("policy", (), {}),
        "vision": ("note", (), {}),
        "wont": ("note", (), {}),
        "skill": ("note", (), {}),
        "cto": ("actor", (), {}),
        "profile": ("actor", (), {}),
        "fix": ("item", (), {}),
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
    p = load_pack("basic", tmp_path)
    assert p.presets["task"].template == "templates/item.md"
    assert p.presets["note"].template == "templates/note.md"
    assert p.presets["code"].template == "templates/code.md"
    assert p.presets["decision"].template == "templates/record.md"
    assert p.presets["check"].template == "templates/check.md"


def test_builtin_pack_ships_body_templates() -> None:
    templates = importlib.resources.files("context_rail").joinpath(
        "packs", "basic", "templates"
    )
    shipped = {entry.name for entry in templates.iterdir()}
    assert {
        "item.md",
        "note.md",
        "code.md",
        "record.md",
        "check.md",
        "charter.md",
        "goal.md",
        "vision.md",
        "scope.md",
        "wont.md",
        "work-order.md",
    } <= shipped


def test_local_pack_overrides_builtin(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("initial", "done")))
    assert load_pack("basic", tmp_path).initial == "done"


def test_local_pack_template_override_is_kept(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("presets.task.template", "templates/task.md")))
    assert load_pack("basic", tmp_path).presets["task"].template == "templates/task.md"


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
        load_pack("basic", tmp_path)
    if mutations == (("version", 2),):
        assert err.value.code in ("SCHEMA_UNSUPPORTED", "VALIDATION_FAILED")
    else:
        assert err.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(("key", "value"), [("stuck_hours", 0.5), ("retries", 0)])
def test_policy_values_are_accepted_and_kept(
    tmp_path: Path, key: str, value: float | int
) -> None:
    _write_pack(tmp_path, _pack((f"policy.{key}", value)))
    assert load_pack("basic", tmp_path).policy[key] == value


@pytest.mark.parametrize("name", ["../small", "a/b", "C:small", "..", "a\\b", ""])
def test_pack_name_escapes_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ModelError) as err:
        load_pack(name, tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_unknown_pack_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelError) as err:
        load_pack("nosuch", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_broken_local_pack_does_not_fall_back_to_builtin(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("initial", "nope")))
    with pytest.raises(ModelError) as err:
        load_pack("basic", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_pack_cache_tracks_path_mtime_size_and_override_presence(
    tmp_path: Path,
) -> None:
    assert load_pack("basic", tmp_path).initial == "todo"

    path = _write_pack(tmp_path, _pack(("initial", "done")))
    assert load_pack("basic", tmp_path).initial == "done"

    size_with_done = path.stat().st_size
    _write_pack(tmp_path, _pack(("initial", "todo")))
    assert path.stat().st_size == size_with_done
    stale = path.stat()
    os.utime(path, ns=(stale.st_atime_ns, stale.st_mtime_ns + 2_000_000_000))
    assert load_pack("basic", tmp_path).initial == "todo"

    path.unlink()
    assert load_pack("basic", tmp_path).initial == "todo"


@pytest.mark.parametrize(
    "template",
    [
        "../../outside.md",
        "../outside.md",
        "/outside.md",
        "C:/outside.md",
        "templates\\item.md",
        "templates//item.md",
    ],
)
def test_a_template_must_be_a_safe_relative_path(
    tmp_path: Path, template: str
) -> None:
    _write_pack(tmp_path, _pack(("presets.task.template", template)))

    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)

    assert caught.value.code == "VALIDATION_FAILED"


def test_duplicate_policy_keys_refuse(tmp_path: Path) -> None:
    path = tmp_path / "packs" / "basic" / "pack.yaml"
    path.parent.mkdir(parents=True)
    text = yaml.safe_dump(_pack(), sort_keys=False)
    text = text.replace("policy:\n", "policy:\n  retries: 1\n", 1)
    path.write_text(text, encoding="utf-8", newline="\n")

    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)

    assert caught.value.code == "VALIDATION_FAILED"


# ── workflows ─────────────────────────────────────────────────────────────

_SPRINT = {
    "initial": "todo",
    "transitions": {"todo": ["doing", "dropped"], "doing": ["done"], "done": []},
}
_BUGFIX = {
    "initial": "doing",
    "transitions": {"doing": ["done"], "done": ["doing"]},
    "execution": {"done": {"dispatch": False}},
    "quality": {"done": {"gate": "bug_done", "checks": ["repro-check"]}},
}


def test_a_top_level_pack_loads_one_default_workflow(tmp_path: Path) -> None:
    pack = load_pack("basic", tmp_path)
    default = pack.workflow("default")

    assert set(pack.workflows) == {"default"}
    assert default.initial == pack.initial
    assert default.transitions == pack.transitions
    assert default.execution == pack.execution
    assert default.quality == {}
    assert pack.presets["task"].workflow == "default"
    assert pack.workflow_for("task") is default


def test_a_pack_can_declare_named_workflows(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        _pack(("workflows", {"sprint": _SPRINT, "bugfix": _BUGFIX})),
    )

    pack = load_pack("basic", tmp_path)

    assert set(pack.workflows) == {"default", "sprint", "bugfix"}
    assert pack.workflow("sprint") == Workflow(
        name="sprint",
        initial="todo",
        transitions={
            "todo": ("doing", "dropped"),
            "doing": ("done",),
            "done": (),
        },
        execution={},
        quality={},
        statuses=pack.statuses,
    )
    bugfix = pack.workflow("bugfix")
    assert bugfix.initial == "doing"
    assert bugfix.execution == {"done": {"dispatch": False}}
    assert bugfix.quality == {
        "done": QualityRule(gate="bug_done", checks=("repro-check",))
    }


def test_a_preset_selects_its_workflow(tmp_path: Path) -> None:
    payload = _pack(("workflows", {"bugfix": _BUGFIX}))
    payload["presets"]["bug"] = {
        "primitive": "item",
        "tags": ["bug"],
        "workflow": "bugfix",
    }
    _write_pack(tmp_path, payload)

    pack = load_pack("basic", tmp_path)

    assert pack.presets["bug"].workflow == "bugfix"
    assert pack.workflow_for("bug").name == "bugfix"
    assert pack.workflow_for("task").name == "default"


def test_default_is_a_reserved_workflow_name(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("workflows", {"default": _SPRINT})))

    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)

    assert "default" in caught.value.message


@pytest.mark.parametrize(
    "workflow",
    [
        {"initial": "ghost", "transitions": {}},
        {"initial": "todo", "transitions": {"todo": ["ghost"]}},
        {"initial": "todo", "transitions": {}, "execution": {"ghost": {}}},
        {"initial": "todo", "transitions": {}, "quality": {"ghost": {"gate": "x"}}},
        {
            "initial": "todo",
            "transitions": {},
            "quality": {"done": {"gate": "x", "checks": [""]}},
        },
        {"initial": "todo", "transitions": {}, "quality": {"done": {"checks": []}}},
        {"initial": "todo", "transitions": {}, "quality": {"done": {"gate": "Bad Gate"}}},
    ],
)
def test_a_workflow_must_reference_declared_statuses_and_safe_names(
    tmp_path: Path, workflow: dict
) -> None:
    _write_pack(tmp_path, _pack(("workflows", {"broken": workflow})))

    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)

    assert caught.value.code == "VALIDATION_FAILED"


def test_a_workflow_refuses_an_unknown_or_missing_key(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        _pack(("workflows", {"sprint": {**_SPRINT, "label": "Sprint"}})),
    )
    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)
    assert "unknown key" in caught.value.message

    _write_pack(tmp_path, _pack(("workflows", {"sprint": {"transitions": {}}})))
    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)
    assert "missing" in caught.value.message


def test_a_preset_may_not_name_an_unknown_workflow(tmp_path: Path) -> None:
    _write_pack(tmp_path, _pack(("presets.task.workflow", "ghost")))

    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)

    assert "ghost" in caught.value.message


def test_container_is_tri_state_and_item_only(tmp_path: Path) -> None:
    for index, value in enumerate((None, True, False)):
        root = tmp_path / f"value-{index}"
        _write_pack(root, _pack(("presets.task.container", value)))
        assert load_pack("basic", root).presets["task"].container is value

    _write_pack(tmp_path, _pack(("presets.note.container", True)))
    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)
    assert "item preset" in caught.value.message

    _write_pack(tmp_path, _pack(("presets.task.container", "yes")))
    with pytest.raises(ModelError) as caught:
        load_pack("basic", tmp_path)
    assert "true, false or null" in caught.value.message


@pytest.mark.parametrize("name", ["basic", "flow", "scrum", "waterfall"])
def test_every_builtin_pack_loads_with_one_default_workflow(
    tmp_path: Path, name: str
) -> None:
    pack = load_pack(name, tmp_path)

    assert set(pack.workflows) == {"default"}
    assert all(preset.workflow == "default" for preset in pack.presets.values())
    assert all(
        preset.container is not None
        for preset in pack.presets.values()
        if preset.primitive == "item"
    )


def test_a_workflow_change_changes_the_pack_hash(tmp_path: Path) -> None:
    from context_rail.store.index import pack_hash

    base = load_pack("basic", tmp_path)
    _write_pack(tmp_path, _pack(("workflows", {"sprint": _SPRINT})))
    with_sprint = load_pack("basic", tmp_path)
    _write_pack(tmp_path, _pack(("workflows", {"sprint": _BUGFIX})))
    with_bugfix = load_pack("basic", tmp_path)

    assert len({pack_hash(base), pack_hash(with_sprint), pack_hash(with_bugfix)}) == 3
