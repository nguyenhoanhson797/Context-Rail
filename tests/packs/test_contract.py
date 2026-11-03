"""The pack contract: what a pack must declare before a store can run on it."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from context_rail.gateway.gateway import invoke
from helpers import init_store, record_inventory, write_pack


def _basic_document() -> dict:
    text = (
        resources.files("context_rail")
        .joinpath("packs", "basic", "pack.yaml")
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(text)


def test_pack_validate_refuses_a_missing_required_preset(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["name"] = "slim"
    del document["presets"]["decision"]
    write_pack(store, "slim", document)

    result = invoke("pack validate", {"name": "slim"}, store=store, actor="human")

    assert result["ok"] is True
    assert result["value"]["valid"] is False
    messages = " ".join(problem["message"] for problem in result["value"]["problems"])
    assert "decision" in messages


def test_pack_validate_refuses_a_preset_template_that_does_not_exist(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["name"] = "slim"
    document["presets"]["task"]["template"] = "templates/missing.md"
    write_pack(store, "slim", document)

    result = invoke("pack validate", {"name": "slim"}, store=store, actor="human")

    assert result["value"]["valid"] is False
    assert any(
        "templates/missing.md" in problem["path"]
        for problem in result["value"]["problems"]
    )


def test_pack_use_refuses_a_target_that_cannot_serve_the_manifest(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["name"] = "slim"
    del document["presets"]["decision"]
    write_pack(store, "slim", document)

    before = (store / "rail.yaml").read_bytes()
    records = record_inventory(store)
    etag = _manifest_etag(store)
    result = invoke(
        "pack use",
        {"name": "slim", "expect": etag, "confirm": "USE PACK slim"},
        store=store,
        actor="human",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert (store / "rail.yaml").read_bytes() == before
    assert record_inventory(store) == records
    assert invoke("status", {}, store=store, actor="cto")["ok"] is True


def test_pack_use_dry_run_refuses_the_same_target(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["name"] = "slim"
    del document["presets"]["decision"]
    write_pack(store, "slim", document)

    result = invoke(
        "pack use",
        {
            "name": "slim",
            "expect": _manifest_etag(store),
            "confirm": "USE PACK slim",
            "dry_run": True,
        },
        store=store,
        actor="human",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_add_refuses_a_template_the_active_pack_does_not_ship(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["presets"]["task"]["template"] = "templates/missing.md"
    write_pack(store, "basic", document)

    result = invoke("add", {"preset": "task", "title": "Task"}, store=store, actor="cto")

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert "templates/missing.md" in result["error"]["message"]


def test_add_refuses_a_value_outside_a_preset_enum(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    result = invoke(
        "add",
        {"preset": "scope", "title": "Scope", "set": ["priority=maybe"]},
        store=store,
        actor="cto",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert result["error"]["details"]["key"] == "priority"
    assert list((store / "project").glob("scope.md")) == []


def test_add_accepts_a_declared_value_and_an_undeclared_field(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    result = invoke(
        "add",
        {
            "preset": "scope",
            "title": "Scope",
            "set": ["priority=must", "estimate=3"],
        },
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result


def test_add_applies_a_preset_default(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["presets"]["task"]["required"] = ["area"]
    document["presets"]["task"]["defaults"] = {"area": "general"}
    write_pack(store, "basic", document)

    created = invoke(
        "add", {"preset": "task", "title": "Defaulted"}, store=store, actor="cto"
    )

    assert created["ok"] is True, created
    text = (store / created["value"]["path"]).read_text(encoding="utf-8")
    assert "area: general" in text


def test_add_refuses_a_missing_required_field(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["presets"]["task"]["required"] = ["area"]
    write_pack(store, "basic", document)

    result = invoke(
        "add", {"preset": "task", "title": "No area"}, store=store, actor="cto"
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert result["error"]["details"]["missing"] == ["area"]
    assert list((store / "items" / "task").glob("no-area.md")) == []


def test_update_revalidates_the_merged_metadata(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _basic_document()
    document["presets"]["task"]["required"] = ["area"]
    document["presets"]["task"]["defaults"] = {"area": "general"}
    write_pack(store, "basic", document)
    created = invoke(
        "add", {"preset": "task", "title": "Merged"}, store=store, actor="cto"
    )
    assert created["ok"] is True, created

    result = invoke(
        "update",
        {
            "slug": "merged",
            "set": ["area="],
            "expect": created["value"]["etag"],
        },
        store=store,
        actor="cto",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert "area" in result["error"]["message"]



def test_update_refuses_a_value_outside_a_preset_enum(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    created = invoke(
        "add", {"preset": "scope", "title": "Scope"}, store=store, actor="cto"
    )["value"]
    refused = invoke(
        "update",
        {
            "slug": created["slug"],
            "set": ["priority=maybe"],
            "expect": created["etag"],
        },
        store=store,
        actor="cto",
    )
    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    accepted = invoke(
        "update",
        {
            "slug": created["slug"],
            "set": ["priority=could"],
            "expect": created["etag"],
        },
        store=store,
        actor="cto",
    )
    assert accepted["ok"] is True, accepted


def test_the_engine_names_one_default_pack() -> None:
    from context_rail.core.defaults import DEFAULT_PACK
    from context_rail.gateway.tools import TOOLS
    from context_rail.model.manifest import DEFAULT_MANIFEST

    assert DEFAULT_MANIFEST["pack"] == DEFAULT_PACK
    init = next(tool for tool in TOOLS if tool.name == "init")
    pack_param = next(param for param in init.params if param.name == "pack")
    assert pack_param.default is None
    create = next(tool for tool in TOOLS if tool.name == "pack create")
    from_param = next(param for param in create.params if param.name == "from")
    assert from_param.default == DEFAULT_PACK


def test_the_builtin_profile_fallback_uses_the_default_pack() -> None:
    from context_rail.core.defaults import DEFAULT_PACK
    from context_rail.model.cto import load_builtin_profile, load_pack_profile

    assert load_builtin_profile("cto") == load_pack_profile(DEFAULT_PACK, "cto")


def _manifest_etag(store: Path) -> str:
    import hashlib

    return hashlib.sha256((store / "rail.yaml").read_bytes()).hexdigest()[:16]
