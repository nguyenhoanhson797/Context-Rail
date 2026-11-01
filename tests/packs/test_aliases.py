"""Deprecated pack names keep resolving, without rewriting an approved store."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.errors import ModelError
from context_rail.model.pack import canonical_pack_name, load_pack
from helpers import init_store, write_pack

LOCAL_DOCUMENT = {
    "name": "small",
    "version": 1,
    "statuses": {"todo": "not_started", "done": "done"},
    "initial": "todo",
    "transitions": {"todo": ["done"], "done": []},
    "presets": {"task": {"primitive": "item", "tags": ["task"]}},
    "policy": {"stuck_hours": 24, "retries": 1},
}


def test_canonical_names_map_the_deprecated_ones() -> None:
    assert canonical_pack_name("small") == "basic"
    assert canonical_pack_name("indie") == "flow"
    assert canonical_pack_name("scrum") == "scrum"
    assert canonical_pack_name("nosuch") == "nosuch"


def test_a_deprecated_name_loads_the_successor_pack(tmp_path: Path) -> None:
    small = load_pack("small", tmp_path)
    basic = load_pack("basic", tmp_path)
    assert small.name == "small"
    assert set(small.presets) == set(basic.presets)
    assert small.transitions == basic.transitions

    indie = load_pack("indie", tmp_path)
    flow = load_pack("flow", tmp_path)
    assert indie.name == "indie"
    assert set(indie.presets) == set(flow.presets)
    assert indie.statuses == flow.statuses
    assert {"ready", "review"} <= set(indie.statuses)


def test_a_store_local_pack_named_like_an_alias_still_wins(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = {**LOCAL_DOCUMENT, "initial": "done"}
    write_pack(store, "small", document)

    pack = load_pack("small", store)
    assert pack.name == "small"
    assert pack.initial == "done"


def test_an_unknown_pack_is_still_unknown(tmp_path: Path) -> None:
    with pytest.raises(ModelError) as caught:
        load_pack("smal", tmp_path)
    assert caught.value.code == "VALIDATION_FAILED"


def test_pack_list_shows_the_successor_and_marks_it_active(tmp_path: Path) -> None:
    store = init_store(tmp_path, pack="small")
    value = invoke("pack list", {}, store=store, actor="human")["value"]
    names = {row["name"] for row in value["packs"]}
    assert {"basic", "flow", "scrum", "waterfall"} == names
    active = [row["name"] for row in value["packs"] if row["active"]]
    assert active == ["basic"]


def test_pack_show_resolves_an_alias_and_keeps_the_approved_target(tmp_path: Path) -> None:
    store = init_store(tmp_path, pack="small")
    value = invoke("pack show", {"name": "small"}, store=store, actor="human")["value"]
    assert value["pack"] == "basic"
    assert value["approval"]["target"] == "pack:small"


def test_pack_validate_accepts_an_alias(tmp_path: Path) -> None:
    store = init_store(tmp_path, pack="small")
    result = invoke("pack validate", {"name": "small"}, store=store, actor="human")
    assert result["ok"] is True and result["value"]["valid"] is True


def test_pack_create_from_an_alias_copies_the_successor(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    result = invoke(
        "pack create", {"name": "mine", "from": "small"}, store=store, actor="cto"
    )
    assert result["ok"] is True, result
    assert "name: mine" in (store / "packs" / "mine" / "pack.yaml").read_text(
        encoding="utf-8"
    )
    assert load_pack("mine", store).presets["bug"].primitive == "item"


def test_packaged_profiles_and_skills_resolve_an_alias() -> None:
    from context_rail.model.cto import load_pack_profile, load_pack_skills

    assert load_pack_skills("small") == load_pack_skills("basic")
    assert load_pack_profile("small", "cto") == load_pack_profile("basic", "cto")
    assert load_pack_skills("indie") == load_pack_skills("flow")


def test_a_deprecated_store_reads_the_successor_pack_templates(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path, pack="small")
    target = store / "packs" / "basic" / "templates" / "item.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# {title}\n\nFrom the successor pack.\n", encoding="utf-8", newline="\n"
    )
    created = invoke("add", {"preset": "goal", "title": "Goal"}, store=store, actor="cto")
    assert created["ok"] is True, created
    text = (store / created["value"]["path"]).read_text(encoding="utf-8")
    assert "From the successor pack." in text
