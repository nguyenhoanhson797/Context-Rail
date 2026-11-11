"""Seat records are engine-owned: only a seat handler may commit one."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import load_manifest
from context_rail.store.write import WriteRefused, commit
from helpers import as_human


def seat_kinds(store: Path) -> list[str]:
    kinds: list[str] = []
    for path in (store / "records").rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        kind = payload.get("kind")
        if isinstance(kind, str) and kind.startswith("seat."):
            kinds.append(kind)
    return kinds


def test_generic_add_cannot_create_a_seat_record(role_store: SimpleNamespace) -> None:
    before = len(seat_kinds(role_store.store))
    with as_human():
        added = invoke(
            "add",
            {"preset": "event", "title": "Not a seat", "set": ["kind=seat.granted"]},
            store=role_store.store,
            actor=None,
        )

    assert len(seat_kinds(role_store.store)) == before
    if not is_ok(added):
        assert added["error"]["code"] == "VALIDATION_FAILED"


def test_direct_commit_of_a_seat_kind_without_the_flag_is_refused(
    role_store: SimpleNamespace,
) -> None:
    manifest = load_manifest(role_store.store)
    record = {
        "kind": "seat.granted",
        "about": [],
        "data": {
            "schema": 1,
            "seat": "forged",
            "role": "lead",
            "anchor": "project",
            "granted_by": "human",
        },
    }

    try:
        commit(role_store.store, manifest, [], record, actor="human")
    except WriteRefused as refused:
        assert refused.code == "VALIDATION_FAILED"
    else:
        raise AssertionError("a seat record was written without the reserved flag")

    with as_human():
        listed = invoke("seat list", {}, store=role_store.store, actor=None)
    assert is_ok(listed), listed
    assert "forged" not in {row["seat"] for row in listed["value"]["seats"]}
