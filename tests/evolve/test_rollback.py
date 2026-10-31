from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.store.doc import parse_doc
from context_rail.store.location import local_dir
from context_rail.store.versions import canonical_target
from context_rail.store.write import repair
from helpers import bump_versioned, latest_record, write_record


def _version_of(store: Path, rel: str) -> int:
    return int(parse_doc(rel, (store / rel).read_bytes()).fields["version"])


def test_rollback_restores_prior_text_into_a_new_version(skill_store, tmp_path) -> None:
    store = skill_store
    rel = "workspace/skills/test-first.md"
    v1_text = (store / rel).read_bytes()
    edit = tmp_path / "v2.md"
    edit.write_text("# Test first\n\nRevised guidance.\n", encoding="utf-8")
    bump_versioned(store, "test-first", body_file=edit)
    bump_versioned(store, "test-first", body_file=edit)
    current = _version_of(store, rel)
    etag = hashlib.sha256((store / rel).read_bytes()).hexdigest()[:16]
    result = invoke("rollback", {"target_class": "skill", "target": rel, "version": 1, "expect": etag}, store=store, actor="cto")
    assert result["ok"] is True
    restored = latest_record(store, "skill.changed")
    assert restored["data"]["before"] == current and restored["data"]["after"] == current + 1
    assert restored["data"]["restored_from_version"] == 1 and restored["data"]["reports"] == []
    assert _version_of(store, rel) == current + 1
    assert parse_doc(rel, (store / rel).read_bytes()).body == parse_doc(rel, v1_text).body


def test_per_profile_skill_is_resolvable_and_traversal_is_forbidden() -> None:
    assert canonical_target("skill", "workspace/profiles/implementer/skills/test-first.md") == (
        "workspace/profiles/implementer/skills/test-first.md"
    )
    from context_rail.store.versions import TraversalRefused
    with pytest.raises(TraversalRefused):
        canonical_target("skill", "../../etc/x")


def test_digest_only_history_refuses_without_git_fallback(skill_store) -> None:
    write_record(skill_store, "01JLEGACY", "skill.changed", {
        "schema": 1, "target_class": "skill", "target": "workspace/skills/test-first.md",
        "before": 1, "after": 2, "before_digest": "0" * 64, "after_digest": "1" * 64,
        "before_etag": "0" * 16, "after_etag": "1" * 16, "reason": "edit", "reports": [], "restored_from_version": None,
    })
    etag = hashlib.sha256((skill_store / "workspace/skills/test-first.md").read_bytes()).hexdigest()[:16]
    result = invoke("rollback", {"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": etag}, store=skill_store, actor="cto")
    assert result["error"]["code"] == "NOT_FOUND"
    assert result["error"]["details"]["reason"] == "prior_content_unavailable"


def test_protected_restore_needs_human_and_the_typed_phrase(skill_store) -> None:
    etag = hashlib.sha256((skill_store / "workspace/skills/test-first.md").read_bytes()).hexdigest()[:16]
    denied = invoke("rollback", {"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": etag}, store=skill_store, actor="cto")
    assert denied["ok"] is False


def test_stale_etag_and_active_runs_refuse(skill_store, inject_fault) -> None:
    stale = invoke("rollback", {"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": "0" * 16}, store=skill_store, actor="cto")
    assert stale["error"]["code"] == "CONFLICT"


def test_crash_after_replace_is_repaired_into_exactly_one_record(skill_store, inject_fault) -> None:
    etag = hashlib.sha256((skill_store / "workspace/skills/test-first.md").read_bytes()).hexdigest()[:16]
    inject_fault("context_rail.store.write", "_create_record", nth=1, mode="raise")
    invoke("rollback", {"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": etag, "key": "R1"}, store=skill_store, actor="cto")
    assert repair(skill_store)["unrecoverable"] == []
    assert not any((local_dir(skill_store) / "intents").glob("*.json"))
