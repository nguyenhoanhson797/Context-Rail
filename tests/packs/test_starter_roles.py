"""Starter roles: a role block generated from any pack, and `pack create --roles`."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import init_store, write_pack

import context_rail
from context_rail.gateway.gateway import invoke
from context_rail.model.errors import ModelError
from context_rail.model.operating import HierarchyLevel
from context_rail.model.pack import load_pack
from context_rail.model.roles import parse_roles
from context_rail.model.starter import starter_roles

SHIPPED = ["basic", "flow", "scrum", "video", "waterfall"]

_OLD_PACK: dict[str, Any] = {
    "name": "old",
    "version": 1,
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


def _levels(pack: Any) -> list[str]:
    return (
        []
        if pack.operating is None
        else [level.preset for level in pack.operating.hierarchy]
    )


@pytest.mark.parametrize("name", SHIPPED)
def test_starter_roles_parse_for_every_shipped_pack(name: str, tmp_path: Path) -> None:
    pack = load_pack(name, tmp_path)

    raw = starter_roles(pack)
    roles = parse_roles(raw, pack)

    expected = [
        "lead",
        *[f"{preset}-lead" for preset in _levels(pack)],
        "implementer",
        "reviewer",
    ]
    assert list(roles) == expected
    assert roles["lead"].anchor.kind == "project"
    assert not {"dispatch", "merge", "tick"} & set(roles["lead"].can.methods)
    assert set(roles["implementer"].can.methods) == {"candidate", "report"}
    assert set(roles["lead"].can.methods["grant"]) == set(expected) - {"lead"}
    assert set(roles["lead"].can.methods["actions"]) == set(pack.actions)
    assert roles["implementer"].workspace == "write"
    assert roles["reviewer"].workspace == "read"
    assert roles["reviewer"].independent_of == ("produce",)
    assert set(roles["reviewer"].can.methods) == {"review", "report"}
    assert [term.kind for term in roles["reviewer"].write] == ["anchor"]
    assert roles["reviewer"].code_write == ()
    for preset in _levels(pack):
        lead = roles[f"{preset}-lead"]
        assert lead.anchor.kind == "level" and lead.anchor.presets == (preset,)
        assert set(lead.can.methods["grant"]) == {"implementer", "reviewer"}


@pytest.mark.parametrize("name", SHIPPED)
def test_starter_roles_never_write_engine_or_policy_documents(
    name: str, tmp_path: Path
) -> None:
    pack = load_pack(name, tmp_path)

    roles = parse_roles(starter_roles(pack), pack)

    for role in roles.values():
        for key in ("add", "update", "rename"):
            for preset in role.can.methods.get(key, ()):
                assert pack.presets[preset].primitive in ("item", "note", "record")
        assert role.human is False


def test_starter_roles_are_deterministic(tmp_path: Path) -> None:
    pack = load_pack("video", tmp_path)

    assert starter_roles(pack) == starter_roles(pack)


def test_two_levels_that_make_the_same_role_name_are_refused(tmp_path: Path) -> None:
    pack = load_pack("video", tmp_path)
    levels = (HierarchyLevel("scene_x"), HierarchyLevel("scene-x"))
    clash = replace(pack, operating=replace(pack.operating, hierarchy=levels))

    with pytest.raises(ModelError, match="same level-lead role name"):
        starter_roles(clash)


def test_a_pack_without_leaf_presets_gets_no_workers(tmp_path: Path) -> None:
    pack = load_pack("video", tmp_path)
    containers = {
        name: replace(preset, container=True) if preset.primitive == "item" else preset
        for name, preset in pack.presets.items()
    }

    roles = starter_roles(replace(pack, presets=containers))

    assert "implementer" not in roles and "reviewer" not in roles
    assert roles["lead"]["can"]["grant"] == ["scene-lead"]
    assert roles["scene-lead"]["can"]["grant"] == []


def test_budgets_follow_the_role_kind(tmp_path: Path) -> None:
    pack = load_pack("video", tmp_path)

    roles = parse_roles(starter_roles(pack), pack)

    assert (roles["lead"].sees.chars, roles["lead"].sees.docs) == (12000, 60)
    assert (roles["scene-lead"].sees.chars, roles["scene-lead"].sees.docs) == (8000, 40)
    assert (roles["implementer"].sees.chars, roles["implementer"].sees.docs) == (
        16000,
        20,
    )
    assert (roles["reviewer"].sees.chars, roles["reviewer"].sees.docs) == (16000, 20)


def test_pack_create_roles_writes_a_valid_draft(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    created = invoke(
        "pack create",
        {"name": "mine", "from": "video", "roles": True},
        store=store,
        actor="cto",
    )

    assert created["ok"] is True, created
    text = (store / "packs" / "mine" / "pack.yaml").read_text(encoding="utf-8")
    assert "&id" not in text and "*id" not in text
    draft = yaml.safe_load(text)
    assert draft["operating"]["version"] == 2
    assert {"lead", "scene-lead", "implementer", "reviewer"} <= set(
        draft["operating"]["roles"]
    )
    assert draft["operating"]["unit"] == "shot"
    checked = invoke("pack validate", {"name": "mine"}, store=store, actor="cto")
    assert checked["ok"] is True and checked["value"]["valid"] is True
    assert list(load_pack("mine", store).operating.roles) == list(
        draft["operating"]["roles"]
    )


def test_pack_create_without_roles_is_unchanged(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    created = invoke(
        "pack create", {"name": "mine", "from": "video"}, store=store, actor="cto"
    )

    assert created["ok"] is True, created
    text = (store / "packs" / "mine" / "pack.yaml").read_text(encoding="utf-8")
    source = (
        Path(context_rail.__file__).parent / "packs" / "video" / "pack.yaml"
    ).read_text(encoding="utf-8")
    assert text == source.replace("name: video", "name: mine", 1)


def test_pack_create_roles_refuses_a_version_1_pack(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    write_pack(store, "old", copy.deepcopy(_OLD_PACK))

    refused = invoke(
        "pack create",
        {"name": "mine", "from": "old", "roles": True},
        store=store,
        actor="cto",
    )

    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert "version" in refused["error"]["message"]
    assert not (store / "packs" / "mine").exists()
