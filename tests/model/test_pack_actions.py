"""Declarative pack actions: strict shape, safe tools and safe templates."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.actions import Action, Requires
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack

BASE: dict[str, Any] = {
    "name": "basic",
    "version": 1,
    "statuses": {"todo": "not_started", "doing": "active", "done": "done"},
    "initial": "todo",
    "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
    "presets": {
        "task": {"primitive": "item", "tags": ["task"], "container": False},
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": []},
        "event": {"primitive": "record", "tags": []},
        "check": {"primitive": "check", "tags": []},
        "charter": {"primitive": "policy", "tags": []},
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
        "vision": {"primitive": "note", "tags": []},
        "wont": {"primitive": "note", "tags": []},
        "skill": {"primitive": "note", "tags": []},
        "cto": {"primitive": "actor", "tags": []},
        "profile": {"primitive": "actor", "tags": []},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
}

ACTION: dict[str, Any] = {
    "on": "task",
    "from": ["todo"],
    "tool": "change move",
    "args": {"slug": "{item}", "status": "done", "expect": "{etag}"},
    "use_when": "when the task is finished",
    "requires": {"children": True, "check": "done-check", "review": True},
}


def _load(tmp_path: Path, action: dict[str, Any], name: str = "finish") -> Any:
    payload = copy.deepcopy(BASE)
    payload["actions"] = {name: action}
    path = tmp_path / "packs" / "basic" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_pack("basic", tmp_path)


def test_an_action_parses_with_its_full_shape(tmp_path: Path) -> None:
    pack = _load(tmp_path, ACTION)

    action = pack.action("finish")

    assert action == Action(
        name="finish",
        on="task",
        from_states=("todo",),
        requires=Requires(children=True, check="done-check", review=True),
        tool="move",
        args={"slug": "{item}", "status": "done", "expect": "{etag}"},
        use_when="when the task is finished",
        version=1,
    )


def test_action_version_defaults_and_round_trips(tmp_path: Path) -> None:
    pack = _load(tmp_path, {**ACTION, "version": 3})
    assert pack.action("finish").version == 3


@pytest.mark.parametrize(
    "mutation",
    [
        {"unknown": 1},
        {"tool": "do"},
        {"tool": "merge"},
        {"tool": "adapters sync"},
        {"tool": "doctor"},
        {"on": "ghost"},
        {"from": ["ghost"]},
        {"from": "todo"},
        {"use_when": ""},
        {"version": 0},
        {"args": {"path": "x"}},
        {"args": {"slug": "{actor}"}},
        {"requires": {"unknown": True}},
        {"requires": {"children": "yes"}},
        {"requires": {"field": "bad name"}},
    ],
)
def test_an_invalid_action_is_refused_at_load(
    tmp_path: Path, mutation: dict[str, Any]
) -> None:
    with pytest.raises(ModelError) as caught:
        _load(tmp_path, {**ACTION, **mutation})

    assert caught.value.code == "VALIDATION_FAILED"


def test_a_star_action_applies_to_any_preset(tmp_path: Path) -> None:
    pack = _load(tmp_path, {**ACTION, "on": "*"})
    assert pack.action("finish").on == "*"
