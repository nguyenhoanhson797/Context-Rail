from __future__ import annotations

import json
import subprocess
import sys

from context_rail.gateway.gateway import invoke


def test_driver_without_scratch_context_refuses_and_changes_nothing(skill_store, monkeypatch) -> None:
    monkeypatch.delenv("RAIL_SYNC_SCRATCH", raising=False)
    before = (skill_store / "workspace/skills/test-first.md").read_bytes()
    result = invoke("sync driver", {"kind": "doc", "base": "b", "ours": "o", "theirs": "t", "path": "p"}, store=skill_store, actor="cto")
    assert result["error"]["code"] == "INVALID_STATE"
    assert (skill_store / "workspace/skills/test-first.md").read_bytes() == before


def test_disjoint_doc_changes_exit_zero_and_write_only_scratch(sync_scratch) -> None:
    base = sync_scratch.scratch / "base.md"
    ours = sync_scratch.scratch / "ours.md"
    theirs = sync_scratch.scratch / "theirs.md"
    base.write_text("---\ntitle: One\n---\nfirst\n\nlast\n", encoding="utf-8")
    ours.write_text("---\ntitle: One\n---\nfirst edited\n\nlast\n", encoding="utf-8")
    theirs.write_text("---\ntitle: One\n---\nfirst\n\nlast edited\n", encoding="utf-8")
    result = invoke("sync driver", {"kind": "doc", "base": str(base), "ours": str(ours), "theirs": str(theirs), "path": "items/task/x.md"}, store=sync_scratch.store, actor="cto")
    assert result["ok"] is True
    assert "<<<<<<<" not in ours.read_text(encoding="utf-8")


def test_conflicting_status_leaves_diagnostic_and_no_markers(sync_scratch) -> None:
    base = sync_scratch.scratch / "base.md"
    ours = sync_scratch.scratch / "ours.md"
    theirs = sync_scratch.scratch / "theirs.md"
    base.write_text("---\nstatus: todo\n---\nbody\n", encoding="utf-8")
    ours.write_text("---\nstatus: doing\n---\nbody\n", encoding="utf-8")
    theirs.write_text("---\nstatus: blocked\n---\nbody\n", encoding="utf-8")
    result = invoke("sync driver", {"kind": "doc", "base": str(base), "ours": str(ours), "theirs": str(theirs), "path": "items/task/x.md"}, store=sync_scratch.store, actor="cto")
    assert result["error"]["code"] == "CONFLICT"
    assert "<<<<<<<" not in ours.read_text(encoding="utf-8")
    assert any((sync_scratch.scratch / "diagnostics").glob("*.json"))


def test_same_record_id_with_different_bytes_conflicts(sync_scratch) -> None:
    base = sync_scratch.scratch / "base.json"
    ours = sync_scratch.scratch / "ours.json"
    theirs = sync_scratch.scratch / "theirs.json"
    ours.write_text(json.dumps({"id": "01J", "kind": "decision", "data": {"v": 1}}), encoding="utf-8")
    theirs.write_text(json.dumps({"id": "01J", "kind": "decision", "data": {"v": 2}}), encoding="utf-8")
    result = invoke("sync driver", {"kind": "record", "base": "", "ours": str(ours), "theirs": str(theirs), "path": "records/2026-10/01J.json"}, store=sync_scratch.store, actor="cto")
    assert result["error"]["code"] == "CONFLICT"


def test_config_divergence_conflicts_without_fieldwise_merge(sync_scratch) -> None:
    base = sync_scratch.scratch / "base.yaml"
    ours = sync_scratch.scratch / "ours.yaml"
    theirs = sync_scratch.scratch / "theirs.yaml"
    base.write_text("policy:\n  stuck_hours: 24\n", encoding="utf-8")
    ours.write_text("policy:\n  stuck_hours: 48\n", encoding="utf-8")
    theirs.write_text("policy:\n  stuck_hours: 12\n", encoding="utf-8")
    result = invoke("sync driver", {"kind": "config", "base": str(base), "ours": str(ours), "theirs": str(theirs), "path": "rail.yaml"}, store=sync_scratch.store, actor="cto")
    assert result["error"]["code"] == "CONFLICT"


def test_driver_spike_paths_with_spaces_and_unicode(branch_fixture_repo) -> None:
    echo = branch_fixture_repo.root / "echo.py"
    echo.write_text("import sys\nprint(len(sys.argv) - 1)\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(echo), "%O", "%A", "%B", "%P"], capture_output=True, text=True)
    assert out.stdout.strip() == "4"
