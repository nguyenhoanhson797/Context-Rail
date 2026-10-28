"""Approval by meaning: the semantic hashes, the protected set and re-approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.core.util import sha256_hex
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.approval import (
    approval_state,
    protected_snapshot,
    semantic_hash,
    snapshot_digest,
)
from helpers import touch_doc

PACK_KEY = "@pack/small/policy"
MUST = "project/send-invoice.md"
PROTECTED_PATHS = (
    "charter.md",
    "project/invoices-on-autopilot.md",
    "project/send-invoice.md",
    "project/ship-invoicing.md",
    "project/track-payment.md",
    "project/wont.md",
    "rail.yaml",
)


def _context(store: Path) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("small", store),
        actor="human",
    )


def _approval_records(store: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "approval.recorded":
            found.append(payload)
    return found


def _show(store: Path, slug: str = "charter") -> dict[str, Any]:
    return invoke("show", {"slug": slug}, store=store, actor="human")["value"]


def _state(store: Path) -> dict[str, Any]:
    return _show(store)["approval"]


def test_approve_records_semantic_hashes_of_the_protected_set(
    approved_store: Path,
) -> None:
    records = _approval_records(approved_store)
    assert len(records) == 1
    record = records[0]
    assert record["actor"] == "human"
    assert record["about"] == ["charter"]
    data = record["data"]
    assert set(data) == {"schema", "target", "protected_hashes", "confirmer", "confirmation"}
    assert data["schema"] == 1
    assert data["target"] == "charter"
    assert data["confirmer"] == "validator"
    assert data["confirmation"] == "APPROVE charter"

    hashes = data["protected_hashes"]
    assert set(hashes) == {*PROTECTED_PATHS, PACK_KEY}
    for rel in PROTECTED_PATHS:
        data_bytes = (approved_store / rel).read_bytes()
        expected = semantic_hash(
            data_bytes, format="yaml" if rel.endswith(".yaml") else "markdown"
        )
        assert hashes[rel] == expected, rel
        assert len(hashes[rel]) == 64 and hashes[rel] == hashes[rel].lower()
    policy = load_pack("small", approved_store).policy
    policy_bytes = yaml.safe_dump(policy, sort_keys=True).encode("utf-8")
    assert hashes[PACK_KEY] == semantic_hash(policy_bytes, format="yaml")

    context = _context(approved_store)
    try:
        assert snapshot_digest(hashes) == approval_state(context, "charter")["snapshot_digest"]
    finally:
        context.close()


def test_approve_result_names_the_record_target_and_hashes(
    tmp_path: Path, answers: Path
) -> None:
    assert is_ok(invoke("init", {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"}, actor="human"))
    store = tmp_path / "context-rail"

    result = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
        store=store,
        actor="human",
    )

    assert is_ok(result), result
    value = result["value"]
    assert set(value) == {"approval", "target", "hashes"}
    assert value["target"] == "charter"
    assert value["hashes"] == _approval_records(store)[0]["data"]["protected_hashes"]
    assert value["approval"] == _approval_records(store)[0]["id"]


def test_approve_refuses_a_wrong_phrase_and_records_nothing(
    tmp_path: Path, answers: Path
) -> None:
    assert is_ok(invoke("init", {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"}, actor="human"))
    store = tmp_path / "context-rail"

    for phrase in ("approve charter", "APPROVE  charter", ""):
        result = invoke(
            "approve",
            {"target": "charter", "confirm": phrase, "confirmer": "Val"},
            store=store,
            actor="human",
        )
        assert result["error"]["code"] == "VALIDATION_FAILED", result
    assert _approval_records(store) == []
    assert _state(store)["state"] == "missing"


def test_a_cto_cannot_record_an_approval(tmp_path: Path, answers: Path) -> None:
    assert is_ok(invoke("init", {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"}, actor="human"))
    store = tmp_path / "context-rail"

    result = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
        store=store,
        actor="cto",
    )

    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert _approval_records(store) == []


def test_show_charter_reports_the_state_digest_and_phrase(
    approved_store: Path,
) -> None:
    shown = _show(approved_store)
    approval = shown["approval"]
    record = _approval_records(approved_store)[0]
    assert approval["target"] == "charter"
    assert approval["state"] == "approved"
    assert approval["latest_record"] == record["id"]
    assert approval["protected_hashes"] == record["data"]["protected_hashes"]
    assert shown["phrase"] == "APPROVE charter"

    context = _context(approved_store)
    try:
        state = approval_state(context, "charter")
    finally:
        context.close()
    assert approval["snapshot_digest"] == state["snapshot_digest"]
    assert len(approval["snapshot_digest"]) == 64


def test_lifecycle_moves_keep_approval_and_meaning_edits_break_it(
    approved_store: Path,
) -> None:
    before = _state(approved_store)
    etag = invoke("show", {"slug": "send-invoice"}, store=approved_store)["value"]["doc"]["etag"]

    moved = invoke(
        "move",
        {"slug": "send-invoice", "status": "doing", "expect": etag},
        store=approved_store,
        actor="cto",
    )

    assert is_ok(moved), moved
    after = _state(approved_store)
    assert after["state"] == "approved"
    assert after["snapshot_digest"] == before["snapshot_digest"]

    touch_doc(approved_store, MUST, estimate=5)
    assert _state(approved_store)["state"] == "mismatch"

    touch_doc(approved_store, MUST, estimate=6)
    assert _state(approved_store)["state"] == "mismatch"


def test_crlf_bom_and_updated_only_keep_the_approval(approved_store: Path) -> None:
    digest = _state(approved_store)["snapshot_digest"]
    path = approved_store / MUST
    text = path.read_text(encoding="utf-8")

    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert _state(approved_store)["state"] == "approved"

    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert _state(approved_store)["state"] == "approved"

    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").lstrip(b"\xef\xbb\xbf"))
    touch_doc(approved_store, MUST, updated="2030-01-01T00:00:00Z")
    assert _state(approved_store)["state"] == "approved"
    assert _state(approved_store)["snapshot_digest"] == digest


def test_reapproval_needs_the_current_snapshot_digest(approved_store: Path) -> None:
    stale = _state(approved_store)["snapshot_digest"]
    first = _approval_records(approved_store)[0]
    first_path = next(
        path
        for path in sorted((approved_store / "records").rglob("*.json"))
        if json.loads(path.read_text(encoding="utf-8"))["id"] == first["id"]
    )
    first_bytes = first_path.read_bytes()

    touch_doc(approved_store, "charter.md", remote="upstream")
    assert _state(approved_store)["state"] == "mismatch"

    refused = invoke(
        "approve",
        {
            "target": "charter",
            "confirm": "APPROVE charter",
            "confirmer": "Val",
            "expect": stale,
        },
        store=approved_store,
        actor="human",
    )
    assert refused["error"]["code"] == "CONFLICT"
    assert len(_approval_records(approved_store)) == 1

    accepted = invoke(
        "approve",
        {
            "target": "charter",
            "confirm": "APPROVE charter",
            "confirmer": "Val",
            "expect": _state(approved_store)["snapshot_digest"],
        },
        store=approved_store,
        actor="human",
    )

    assert is_ok(accepted), accepted
    assert len(_approval_records(approved_store)) == 2
    assert _state(approved_store)["state"] == "approved"
    assert first_path.read_bytes() == first_bytes


def test_acceptance_checks_join_and_leave_the_protected_set(
    approved_store: Path,
) -> None:
    before = _state(approved_store)
    check = (
        "---\n"
        "primitive: check\n"
        "preset: check\n"
        "title: Sign off\n"
        "tags: []\n"
        "schema: 1\n"
        "mode: human\n"
        "acceptance: true\n"
        "verifies: '[[send-invoice]]'\n"
        "---\n"
        "The human signs the login page off.\n"
    )
    (approved_store / "checks").mkdir(exist_ok=True)
    (approved_store / "checks" / "signoff.md").write_text(check, encoding="utf-8")

    context = _context(approved_store)
    try:
        hashes = protected_snapshot(context)
    finally:
        context.close()
    assert "checks/signoff.md" in hashes
    assert hashes[PACK_KEY] == before["protected_hashes"][PACK_KEY]
    for rel in PROTECTED_PATHS:
        assert hashes[rel] == before["protected_hashes"][rel], rel
    assert _state(approved_store)["state"] == "mismatch"

    (approved_store / "checks" / "signoff.md").unlink()
    assert _state(approved_store)["state"] == "approved"


def test_a_missing_protected_document_is_refused(approved_store: Path) -> None:
    path = approved_store / "project" / "wont.md"
    path.unlink()

    context = _context(approved_store)
    try:
        hashes = protected_snapshot(context)
    finally:
        context.close()
    assert hashes["project/wont.md"] is None
    assert _state(approved_store)["state"] == "mismatch"

    refused = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
        store=approved_store,
        actor="human",
    )
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert len(_approval_records(approved_store)) == 1


def test_a_replayed_approval_key_writes_one_record(approved_store: Path) -> None:
    args = {
        "target": "charter",
        "confirm": "APPROVE charter",
        "confirmer": "Val",
        "key": "approve-once",
    }

    first = invoke("approve", args, store=approved_store, actor="human")
    second = invoke("approve", args, store=approved_store, actor="human")

    assert is_ok(first) and is_ok(second), (first, second)
    assert second["value"]["approval"] == first["value"]["approval"]
    assert len(_approval_records(approved_store)) == 2


def test_approving_a_pack_candidate_uses_the_planned_pack(
    approved_store: Path,
) -> None:
    result = invoke(
        "approve",
        {"target": "pack:small", "confirm": "APPROVE pack:small", "confirmer": "Val"},
        store=approved_store,
        actor="human",
    )

    assert is_ok(result), result
    assert "@pack/small/policy" in result["value"]["hashes"]
    shown = _show(approved_store, "pack:small")
    assert shown["target"] == "pack:small"
    assert shown["approval"]["state"] == "approved"
    assert shown["phrase"] == "APPROVE pack:small"
    assert _state(approved_store)["state"] == "approved"


def test_semantic_hash_covers_meaning_not_lifecycle_fields() -> None:
    first = b"---\nprimitive: item\ntitle: Login\nstatus: todo\n---\nBody text.\n"
    lifecycle = (
        b"---\nprimitive: item\ntitle: Login\nstatus: doing\nresolution: shipped\n"
        b"updated: '2030-01-01T00:00:00Z'\n---\nBody text.\n"
    )
    assert semantic_hash(first, format="markdown") == semantic_hash(
        lifecycle, format="markdown"
    )

    retitled = b"---\nprimitive: item\ntitle: Login page\nstatus: todo\n---\nBody text.\n"
    reworded = b"---\nprimitive: item\ntitle: Login\nstatus: todo\n---\nBody text!\n"
    extra_key = b"---\nprimitive: item\ntitle: Login\nstatus: todo\nowner: me\n---\nBody text.\n"
    for changed in (retitled, reworded, extra_key):
        assert semantic_hash(changed, format="markdown") != semantic_hash(
            first, format="markdown"
        )

    crlf = first.replace(b"\n", b"\r\n")
    bom = b"\xef\xbb\xbf" + first
    assert semantic_hash(crlf, format="markdown") == semantic_hash(first, format="markdown")
    assert semantic_hash(bom, format="markdown") == semantic_hash(first, format="markdown")

    assert semantic_hash(b"a: 1\nstatus: x\n", format="yaml") == semantic_hash(
        b"status: y\nupdated: now\na: 1\n", format="yaml"
    )
    assert semantic_hash(b"a: 1\n", format="yaml") != semantic_hash(
        b"a: 2\n", format="yaml"
    )


def test_semantic_hash_refuses_a_guess() -> None:
    from context_rail.model.errors import ModelError

    with pytest.raises(ModelError):
        semantic_hash(b"a: 1\nb: 2\n", format="toml")
    with pytest.raises(ModelError):
        semantic_hash(b"---\ntitle: \xff\n---\n", format="markdown")
    with pytest.raises(ModelError):
        semantic_hash(b"a: 1\na: 2\n", format="yaml")
    with pytest.raises(ModelError):
        semantic_hash(b"---\n- a\n---\n", format="markdown")
    with pytest.raises(ModelError):
        semantic_hash(b"a: .nan\n", format="yaml")
    with pytest.raises(ModelError):
        semantic_hash(b"---\ntitle: {a: 1\n---\n", format="markdown")


def test_snapshot_digest_covers_the_whole_map() -> None:
    hashes = {"b.md": "1" * 64, "a.md": "2" * 64, "@pack/small/policy": "3" * 64}
    expected = sha256_hex(
        json.dumps(hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )
    assert snapshot_digest(hashes) == expected
    changed = {**hashes, "c.md": "4" * 64}
    assert snapshot_digest(changed) != expected
