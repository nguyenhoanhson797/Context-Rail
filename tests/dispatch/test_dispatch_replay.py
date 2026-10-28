"""Dispatch idempotency: one key, one dispatch, one recorded result."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.gateway.gateway import invoke


def dispatch(leaf: SimpleNamespace, key: str, item: str | None = None) -> dict[str, Any]:
    return invoke(
        "dispatch",
        {"item": item or leaf.slug, "expect": leaf.etag, "key": key},
        store=leaf.store,
        actor="cto",
    )


def records_of(store: Path, kind: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == kind:
            found.append(payload)
    return found


def test_same_key_returns_the_same_result(leaf_item, fake_droid, droid_env) -> None:
    first = dispatch(leaf_item, "d4")
    assert first["ok"], first
    second = dispatch(leaf_item, "d4")
    assert second["ok"], second
    assert second["value"] == first["value"]
    assert len(fake_droid.log()) == 1
    assert len(records_of(leaf_item.store, "run.completed")) == 1
    assert len(records_of(leaf_item.store, "dispatch.started")) == 1

    # The replay survives the seven-day idempotency cache: the header comes
    # from the durable Records, not from a write-pipeline key entry.
    third = dispatch(leaf_item, "d4")
    assert third["value"] == first["value"]
    assert len(fake_droid.log()) == 1


def test_same_key_with_another_item_conflicts(leaf_item, fake_droid, droid_env) -> None:
    first = dispatch(leaf_item, "d5")
    assert first["ok"], first
    added = invoke(
        "add",
        {"preset": "task", "title": "Second page", "rel": ["part_of=send-invoice"]},
        store=leaf_item.store,
        actor="cto",
    )
    assert added["ok"], added
    shown = invoke("show", {"slug": "second-page"}, store=leaf_item.store, actor="cto")
    etag = shown["value"]["doc"]["etag"]

    r = invoke(
        "dispatch",
        {"item": "second-page", "expect": etag, "key": "d5"},
        store=leaf_item.store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "CONFLICT"
    assert len(fake_droid.log()) == 1
    assert len(records_of(leaf_item.store, "run.completed")) == 1


def test_same_key_with_a_stale_expect_conflicts(leaf_item, fake_droid, droid_env) -> None:
    first = dispatch(leaf_item, "d6")
    assert first["ok"], first
    r = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": "0123456789abcdef", "key": "d6"},
        store=leaf_item.store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "CONFLICT"
    assert len(fake_droid.log()) == 1
