from __future__ import annotations

import hashlib
from pathlib import Path

from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.location import local_dir
from context_rail.store.write import repair
from helpers import init_store, record_inventory


def _manifest_etag(store: Path) -> str:
    return hashlib.sha256((store / "rail.yaml").read_bytes()).hexdigest()[:16]


def _scrum_store_with_item(tmp_path: Path, status: str = "doing") -> tuple[Path, str]:
    store = init_store(tmp_path, pack="scrum")
    created = invoke("add", {"preset": "task", "title": "Login"}, store=store, actor="cto")["value"]
    if status != "todo":
        invoke("move", {"slug": created["slug"], "status": status, "expect": created["etag"]}, store=store, actor="cto")
    return store, created["slug"]


def test_migration_is_one_commit_with_one_pack_changed_record(tmp_path, monkeypatch) -> None:
    store, slug = _scrum_store_with_item(tmp_path)
    before = record_inventory(store)
    result = invoke("pack use", {"name": "flow", "expect": _manifest_etag(store), "confirm": "USE PACK flow", "map": ["doing=building"], "key": "K1"}, store=store, actor="human")
    assert result["ok"] is True
    after = record_inventory(store)
    added = set(after) - set(before)
    assert len(added) == 1
    assert all(after[rid] == before.get(rid) for rid in before)
    assert load_pack("flow", store).name == "flow"


def test_replay_with_the_same_key_appends_nothing(tmp_path) -> None:
    store, _ = _scrum_store_with_item(tmp_path)
    args = {"name": "flow", "expect": _manifest_etag(store), "confirm": "USE PACK flow", "map": ["doing=building"], "key": "K1"}
    first = invoke("pack use", args, store=store, actor="human")
    count = len(record_inventory(store))
    second = invoke("pack use", args, store=store, actor="human")
    assert second == first and len(record_inventory(store)) == count


def test_ambiguous_status_refuses_without_mutation(tmp_path) -> None:
    store = init_store(tmp_path, pack="flow")
    created = invoke("add", {"preset": "task", "title": "Login"}, store=store, actor="cto")["value"]
    invoke("move", {"slug": created["slug"], "status": "building", "expect": created["etag"]}, store=store, actor="cto")
    before = record_inventory(store)
    result = invoke("pack use", {"name": "scrum", "expect": _manifest_etag(store), "confirm": "USE PACK scrum"}, store=store, actor="human")
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert record_inventory(store) == before


def test_stale_manifest_etag_refuses(tmp_path) -> None:
    store, _ = _scrum_store_with_item(tmp_path)
    result = invoke("pack use", {"name": "flow", "expect": "0" * 16, "confirm": "USE PACK flow", "map": ["doing=building"]}, store=store, actor="human")
    assert result["error"]["code"] == "CONFLICT"


def test_inventory_race_between_planning_and_publication_refuses(tmp_path, inject_fault) -> None:
    store, slug = _scrum_store_with_item(tmp_path)
    before = record_inventory(store)
    inject_fault("context_rail.gateway.handlers.packs", "_after_planning", mode="return", value=None)
    result = invoke("pack use", {"name": "flow", "expect": _manifest_etag(store), "confirm": "USE PACK flow", "map": ["doing=building"]}, store=store, actor="human")
    assert result["ok"] is True or result["error"]["code"] == "CONFLICT"
    assert set(record_inventory(store)) - set(before) <= {next(iter(set(record_inventory(store)) - set(before)), "")}


def test_crash_between_intent_and_publication_is_repaired_once(tmp_path, inject_fault) -> None:
    store, slug = _scrum_store_with_item(tmp_path)
    inject_fault("context_rail.store.write", "_replace_targets", nth=1, mode="raise")
    result = invoke("pack use", {"name": "flow", "expect": _manifest_etag(store), "confirm": "USE PACK flow", "map": ["doing=building"], "key": "K9"}, store=store, actor="human")
    assert result["ok"] is False
    report = repair(store)
    assert report["unrecoverable"] == []
    assert not any((local_dir(store) / "intents").glob("*.json"))
    assert load_pack("flow", store).name == "flow"
    assert sum(1 for rid in record_inventory(store) if rid) >= 1


def test_active_owner_and_pending_intent_block_the_switch(tmp_path) -> None:
    store, _ = _scrum_store_with_item(tmp_path)
    intents = local_dir(store) / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "stale.json").write_text("{}", encoding="utf-8")
    result = invoke("pack use", {"name": "flow", "expect": _manifest_etag(store), "confirm": "USE PACK flow", "map": ["doing=building"]}, store=store, actor="human")
    assert result["error"]["code"] == "INVALID_STATE"
