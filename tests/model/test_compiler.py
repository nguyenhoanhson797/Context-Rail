"""The static compiler: neutral defaults, declared models and canonical output."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from context_rail.model.compiler import (
    canonical_json,
    compile_operating,
    compile_project,
    semantic_digest,
)
from context_rail.model.pack import load_pack
from context_rail.model.presentation import default_presentation
from helpers import compiled_model, write_pack

_PLAIN: dict[str, Any] = {
    "name": "plain",
    "version": 2,
    "statuses": {
        "todo": "not_started",
        "doing": "active",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "todo",
    "transitions": {
        "todo": ["doing", "dropped"],
        "doing": ["done", "dropped"],
        "done": ["doing"],
        "dropped": ["todo"],
    },
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "task": {"primitive": "item", "tags": ["task"], "container": False},
        "bug": {"primitive": "item", "tags": ["bug"], "container": False},
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
}

_STUDIO: dict[str, Any] = {
    "name": "studio",
    "version": 2,
    "statuses": {
        "draft": "not_started",
        "storyboard": "active",
        "generating": "active",
        "review": "active",
        "approved": "done",
        "blocked": "blocked",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "draft",
    "transitions": {
        "draft": ["storyboard", "generating", "dropped"],
        "storyboard": ["generating", "review", "dropped"],
        "generating": ["review", "blocked", "dropped"],
        "review": ["approved", "generating", "dropped"],
        "approved": ["generating"],
        "blocked": ["generating", "review", "dropped"],
        "done": ["generating"],
        "dropped": ["draft"],
    },
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
        "scene": {"primitive": "item", "tags": ["scene"], "container": True},
        "shot": {"primitive": "item", "tags": ["shot"], "container": False},
        "asset": {"primitive": "item", "tags": ["asset"], "container": False},
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
    },
    "policy": {"stuck_hours": 24, "retries": 1},
    "operating": {
        "version": 1,
        "unit": "shot",
        "hierarchy": [{"preset": "scene", "order": "sequence", "required": True}],
        "focus": {"preset": "scene", "category": "active", "resolve": "unique"},
        "queue": {"presets": ["shot", "asset"], "require": "transition"},
        "progress": {
            "group": "scene",
            "of": ["shot"],
            "complete": {"states": ["approved"]},
        },
        "execution": {"actions": []},
    },
}


def _studio(tmp_path: Path, *mutations: tuple[str, Any]) -> Any:
    payload = copy.deepcopy(_STUDIO)
    for path, value in mutations:
        keys = path.split(".")
        target: Any = payload
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    write_pack(tmp_path, "studio", payload)
    return load_pack("studio", tmp_path)


def test_a_pack_without_operating_gets_the_neutral_model(tmp_path: Path) -> None:
    write_pack(tmp_path, "plain", _PLAIN)
    model = compiled_model(tmp_path, "plain")
    operating = model.operating

    assert operating.unit is None
    assert operating.hierarchy == ()
    assert operating.focus is None
    assert operating.queue.require == "transition"
    assert operating.queue.focus == "priority"
    assert operating.queue.presets == ("bug", "task")
    assert operating.progress.of == ("bug", "task")
    assert operating.progress.group is None
    assert operating.progress.complete.category == "done"
    assert operating.execution_actions == ()


def test_a_declared_operating_model_is_compiled(tmp_path: Path) -> None:
    write_pack(tmp_path, "studio", _STUDIO)
    pack = load_pack("studio", tmp_path)
    model = compile_project(pack, None, default_presentation(pack))
    operating = model.operating

    assert operating.unit == "shot"
    assert [level.preset for level in operating.hierarchy] == ["scene"]
    assert operating.hierarchy[0].order == "sequence"
    assert operating.focus is not None and operating.focus.preset == "scene"
    assert operating.focus.category == "active"
    assert operating.queue.presets == ("shot", "asset")
    assert operating.progress.group == "scene"
    assert operating.progress.of == ("shot",)
    assert operating.progress.complete.states == ("approved",)


def test_progress_defaults_to_the_unit_when_operating_omits_it(
    tmp_path: Path,
) -> None:
    write_pack(
        tmp_path,
        "studio",
        {
            **_STUDIO,
            "operating": {
                key: value
                for key, value in _STUDIO["operating"].items()
                if key != "progress"
            },
        },
    )
    pack = load_pack("studio", tmp_path)

    operating = compile_operating(pack)

    assert operating.progress.of == ("shot",)
    assert operating.progress.complete.category == "done"


def test_the_ir_is_canonical_across_mapping_order(tmp_path: Path) -> None:
    write_pack(tmp_path, "studio", _STUDIO)
    first = load_pack("studio", tmp_path)
    reordered = {
        key: copy.deepcopy(_STUDIO[key])
        for key in ("operating", "presets", "transitions", "initial", "statuses", "name", "version", "policy")
    }
    reordered["presets"] = dict(reversed(list(reordered["presets"].items())))
    reordered["operating"] = {
        key: reordered["operating"][key]
        for key in reversed(list(reordered["operating"]))
    }
    write_pack(tmp_path, "studio", reordered)
    second = load_pack("studio", tmp_path)

    left = compile_project(first, None, default_presentation(first))
    right = compile_project(second, None, default_presentation(second))

    assert left.semantic_digest == right.semantic_digest
    assert left.to_json() == right.to_json()
    assert canonical_json(left.as_dict()) == left.to_json()


def test_a_placement_change_is_semantic(tmp_path: Path) -> None:
    write_pack(tmp_path, "studio", _STUDIO)
    pack = load_pack("studio", tmp_path)

    first = compile_project(
        pack, None, default_presentation(pack), placement={"task": "items/{slug}.md"}
    )
    second = compile_project(
        pack, None, default_presentation(pack), placement={"task": "work/{slug}.md"}
    )

    assert first.semantic_digest != second.semantic_digest
    assert semantic_digest(pack) == compile_project(pack).semantic_digest
