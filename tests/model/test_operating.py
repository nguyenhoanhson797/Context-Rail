"""The v2 pack contract: the optional ``operating`` block fails closed."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.pack import PACK_VERSION, SUPPORTED_PACK_VERSIONS, load_pack
from helpers import write_pack

_DELETE = object()

_BASE: dict[str, Any] = {
    "name": "studio",
    "version": 2,
    "statuses": {
        "draft": "not_started",
        "storyboard": "active",
        "generating": "active",
        "review": "active",
        "approved": "done",
        "active": "active",
        "blocked": "blocked",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "draft",
    "transitions": {
        "draft": ["storyboard", "generating", "active", "dropped"],
        "storyboard": ["generating", "review", "active", "dropped"],
        "generating": ["review", "blocked", "dropped"],
        "review": ["approved", "generating", "blocked", "dropped"],
        "approved": ["generating"],
        "active": ["done", "dropped"],
        "blocked": ["generating", "review", "dropped"],
        "done": ["active"],
        "dropped": ["draft"],
    },
    "execution": {"review": {"dispatch": False}},
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
        "scene": {"primitive": "item", "tags": ["scene"], "container": True},
        "shot": {"primitive": "item", "tags": ["shot"], "container": False},
        "asset": {"primitive": "item", "tags": ["asset"], "container": False},
        "risk": {"primitive": "item", "tags": ["risk"], "container": False},
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
    "actions": {
        "render": {
            "on": "shot",
            "from": ["review"],
            "tool": "move",
            "args": {"slug": "{item}", "status": "approved", "expect": "{etag}"},
            "use_when": "a shot is rendered",
        }
    },
    "operating": {
        "version": 1,
        "unit": "shot",
        "hierarchy": [
            {"preset": "scene", "order": "sequence", "required": True}
        ],
        "focus": {
            "preset": "scene",
            "category": "active",
            "order": "sequence",
            "resolve": "unique",
        },
        "queue": {"presets": ["shot"], "require": "transition", "focus": "priority"},
        "progress": {
            "group": "scene",
            "of": ["shot"],
            "complete": {"states": ["approved"]},
        },
        "execution": {"actions": ["render"]},
    },
}


def _document(*mutations: tuple[str, object]) -> dict[str, Any]:
    payload = copy.deepcopy(_BASE)
    for path, value in mutations:
        keys = path.split(".")
        target: Any = payload
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        last = keys[-1]
        if isinstance(target, list):
            target[int(last)] = value
        elif value is _DELETE:
            del target[last]
        else:
            target[last] = value
    return payload


def _load(tmp_path: Path, *mutations: tuple[str, object]) -> Any:
    write_pack(tmp_path, "studio", _document(*mutations))
    return load_pack("studio", tmp_path)


def test_the_pack_schema_accepts_v1_and_v2() -> None:
    assert PACK_VERSION == 2
    assert SUPPORTED_PACK_VERSIONS == (1, 2)


def test_a_v2_pack_with_operating_loads(tmp_path: Path) -> None:
    pack = _load(tmp_path)

    assert pack.version == 2
    assert pack.operating is not None
    assert pack.operating.unit == "shot"
    assert pack.operating.hierarchy[0].preset == "scene"
    assert pack.operating.focus is not None
    assert pack.operating.focus.category == "active"
    assert pack.operating.queue is not None
    assert pack.operating.queue.presets == ("shot",)
    assert pack.operating.progress is not None
    assert pack.operating.progress.complete.states == ("approved",)
    assert pack.operating.execution.actions == ("render",)


def test_a_v2_pack_may_omit_operating(tmp_path: Path) -> None:
    pack = _load(tmp_path, ("operating", _DELETE))

    assert pack.version == 2
    assert pack.operating is None


def test_a_v1_pack_still_loads_without_operating(tmp_path: Path) -> None:
    pack = _load(tmp_path, ("version", 1), ("operating", _DELETE))

    assert pack.version == 1
    assert pack.operating is None


def test_a_v1_pack_may_not_declare_operating(tmp_path: Path) -> None:
    with pytest.raises(ModelError) as caught:
        _load(tmp_path, ("version", 1))

    assert caught.value.code == "VALIDATION_FAILED"
    assert "version 1" in caught.value.message


@pytest.mark.parametrize(
    "mutations",
    [
        pytest.param((("operating.mood", "light"),), id="unknown-key"),
        pytest.param((("operating.version", 2),), id="unknown-version"),
        pytest.param((("operating.unit", "ghost"),), id="unknown-unit"),
        pytest.param((("operating.unit", "note"),), id="unit-not-an-item"),
        pytest.param(
            (("operating.hierarchy", [{"preset": "shot"}]),),
            id="hierarchy-not-container",
        ),
        pytest.param(
            (
                (
                    "operating.hierarchy",
                    [{"preset": "scene"}, {"preset": "scene"}],
                ),
            ),
            id="duplicate-hierarchy",
        ),
        pytest.param(
            (("operating.hierarchy.0.order", "rank"),),
            id="hierarchy-unknown-order",
        ),
        pytest.param(
            (("operating.focus.preset", "shot"),),
            id="focus-not-in-hierarchy",
        ),
        pytest.param(
            (("operating.focus.category", "finished"),),
            id="unknown-focus-category",
        ),
        pytest.param(
            (("operating.focus.resolve", "nearest"),),
            id="unknown-resolve",
        ),
        pytest.param(
            (("operating.focus.order", "rank"),),
            id="focus-unknown-order",
        ),
        pytest.param(
            (("operating.queue.presets", ["ghost"]),),
            id="queue-unknown-preset",
        ),
        pytest.param(
            (("operating.queue.require", "anything"),),
            id="unknown-require",
        ),
        pytest.param(
            (("operating.queue.focus", "always"),),
            id="unknown-queue-focus",
        ),
        pytest.param(
            (("operating.progress.of", ["ghost"]),),
            id="progress-unknown-preset",
        ),
        pytest.param(
            (("operating.progress.group", "shot"),),
            id="progress-group-not-hierarchy",
        ),
        pytest.param(
            (("operating.progress.complete.states", ["review"]),),
            id="completion-not-done",
        ),
        pytest.param(
            (("operating.progress.complete.category", "active"),),
            id="completion-wrong-category",
        ),
        pytest.param(
            (("operating.execution.actions", ["explode"]),),
            id="unknown-execution-action",
        ),
        pytest.param(
            (("operating.queue.presets", _DELETE),),
            id="queue-missing-presets",
        ),
    ],
)
def test_an_invalid_operating_block_is_refused(
    tmp_path: Path, mutations: tuple[tuple[str, object], ...]
) -> None:
    with pytest.raises(ModelError) as caught:
        _load(tmp_path, *mutations)

    assert caught.value.code == "VALIDATION_FAILED"


def test_the_neutral_operating_block_leaves_every_default_to_the_compiler(
    tmp_path: Path,
) -> None:
    pack = _load(tmp_path, ("operating", _DELETE))

    assert pack.operating is None
    assert pack.presets["scene"].container is True
