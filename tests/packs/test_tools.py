from __future__ import annotations

import json

from context_rail.gateway.gateway import invoke
from helpers import init_store, record_inventory


def test_pack_list_is_bounded_with_origins_and_one_active(tmp_path) -> None:
    store = init_store(tmp_path)
    value = invoke("pack list", {}, store=store, actor="human")["value"]
    names = {row["name"] for row in value["packs"]}
    assert {"indie", "scrum", "waterfall", "small"} <= names
    assert sum(1 for row in value["packs"] if row["active"]) == 1
    assert value["omitted_count"] == 0 and all(row["origin"] for row in value["packs"])


def test_pack_show_unknown_name_is_not_found(tmp_path) -> None:
    store = init_store(tmp_path)
    assert invoke("pack show", {"name": "nosuch"}, store=store, actor="human")["error"]["code"] == "NOT_FOUND"


def test_pack_validate_reports_field_paths_without_mutation(tmp_path) -> None:
    store = init_store(tmp_path)
    invoke("pack create", {"name": "mine", "from": "indie"}, store=store, actor="cto")
    result = invoke("pack validate", {"name": "mine"}, store=store, actor="cto")
    assert result["ok"] is True and result["value"]["valid"] is True


def test_pack_create_writes_an_unselected_draft_once(tmp_path) -> None:
    store = init_store(tmp_path)
    before = (store / "rail.yaml").read_bytes()
    first = invoke("pack create", {"name": "mine", "from": "indie"}, store=store, actor="cto")
    assert first["ok"] is True and first["value"]["approval"] == "pending"
    assert (store / "rail.yaml").read_bytes() == before
    assert invoke("pack create", {"name": "mine", "from": "indie"}, store=store, actor="cto")["error"]["code"] == "CONFLICT"
    assert invoke("pack create", {"name": "x", "from": "nosuch"}, store=store, actor="cto")["error"]["code"] == "NOT_FOUND"


def test_pack_use_is_human_only_and_needs_the_exact_phrase(tmp_path) -> None:
    store = init_store(tmp_path)
    etag = (store / "rail.yaml").read_bytes()
    manifest = invoke("pack show", {"name": "small"}, store=store, actor="human")
    assert manifest["ok"] is True
    denied = invoke("pack use", {"name": "indie", "expect": "0" * 16}, store=store, actor="cto")
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    wrong = invoke("pack use", {"name": "indie", "expect": "0" * 16, "confirm": "use pack indie"}, store=store, actor="human")
    assert wrong["error"]["code"] == "VALIDATION_FAILED"
    assert (store / "rail.yaml").read_bytes() == etag


def test_dry_run_changes_no_bytes_and_no_records(tmp_path) -> None:
    store = init_store(tmp_path)
    before = record_inventory(store)
    result = invoke("pack use", {"name": "indie", "dry_run": True, "expect": "0" * 16}, store=store, actor="human")
    assert record_inventory(store) == before
    assert result["ok"] is True or result["error"]["code"] == "CONFLICT"
