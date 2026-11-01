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


def _manifest_etag(store: Path) -> str:
    import hashlib

    return hashlib.sha256((store / "rail.yaml").read_bytes()).hexdigest()[:16]
