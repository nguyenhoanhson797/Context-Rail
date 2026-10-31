from __future__ import annotations

import hashlib

from context_rail.gateway.gateway import invoke
from helpers import write_versioned_run


def _tree_hash(store) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(store.rglob("*")) if p.is_file() and ".rail-local" not in p.parts}


def test_measure_versions_is_read_only(measure_store, monkeypatch) -> None:
    import subprocess

    def _refuse(*args, **kwargs):
        raise AssertionError("a read executed a process")

    monkeypatch.setattr(subprocess, "run", _refuse)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    before = _tree_hash(measure_store.store)
    result = invoke("measure versions", {"target_class": "skill", "target": "workspace/skills/test-first.md", "before": 1, "after": 2}, store=measure_store.store, actor="cto")
    assert result["ok"] is True
    assert _tree_hash(measure_store.store) == before


def test_rebuilt_index_returns_identical_measurement(measure_store) -> None:
    args = {"target_class": "skill", "target": "workspace/skills/test-first.md", "before": 1, "after": 2, "since": "2026-10-01T00:00:00Z", "until": "2026-10-31T00:00:00Z"}
    first = invoke("measure versions", args, store=measure_store.store, actor="cto")
    measure_store.ix.close()
    (measure_store.store / ".rail-local").mkdir(exist_ok=True)
    invoke("reindex", {"full": True}, store=measure_store.store, actor="cto")
    assert invoke("measure versions", args, store=measure_store.store, actor="cto") == first
