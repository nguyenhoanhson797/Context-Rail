from __future__ import annotations

import hashlib

from context_rail.gateway.gateway import invoke
from helpers import write_attempt


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


def test_measure_usage_is_read_only(measure_store, monkeypatch) -> None:
    import subprocess

    def _refuse(*args, **kwargs):
        raise AssertionError("a read executed a process")

    monkeypatch.setattr(subprocess, "run", _refuse)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    before = _tree_hash(measure_store.store)
    result = invoke("measure usage", {"group_by": "item"}, store=measure_store.store, actor="cto")
    assert result["ok"] is True, result
    assert _tree_hash(measure_store.store) == before


def test_measure_usage_groups_by_model_and_reports_the_whole_window(measure_store) -> None:
    store = measure_store.store
    write_attempt(store, "01JUSGCAND0000000000000001", model="m1", harness="opencode", usage={"tokens": 10}, duration_s=60.0)
    write_attempt(store, "01JUSGCAND0000000000000002", model="m1", harness="opencode", usage={"tokens": 5}, duration_s=120.0)
    write_attempt(store, "01JUSGCAND0000000000000003", model="m2", harness="claude-code", usage={"tokens": "unknown"}, duration_s=30.0)
    result = invoke("measure usage", {"group_by": "model"}, store=store, actor="cto")
    assert result["ok"] is True, result
    value = result["value"]
    assert value["group_by"] == "model"
    assert [row["key"] for row in value["rows"]] == ["m1", "m2"]
    assert value["rows"][0]["candidates"] == 2
    assert value["rows"][0]["usage"]["tokens"] == {"total": 15, "coverage": "complete", "known_n": 2}
    assert value["rows"][1]["usage"]["tokens"]["total"] == "unknown"
    assert value["total"]["candidates"] == 3
    assert value["total"]["duration"] == "70"
    assert value["omitted"] == 0
    assert value["evidence"]["candidates"] == [
        "01JUSGCAND0000000000000001",
        "01JUSGCAND0000000000000002",
        "01JUSGCAND0000000000000003",
    ]


def test_rebuilt_index_returns_identical_usage(measure_store) -> None:
    write_attempt(measure_store.store, "01JUSGCAND0000000000000001", model="m1", usage={"tokens": 10})
    args = {"group_by": "model", "since": "2026-10-01T00:00:00Z", "until": "2026-10-31T00:00:00Z"}
    first = invoke("measure usage", args, store=measure_store.store, actor="cto")
    measure_store.ix.close()
    (measure_store.store / ".rail-local").mkdir(exist_ok=True)
    invoke("reindex", {"full": True}, store=measure_store.store, actor="cto")
    assert invoke("measure usage", args, store=measure_store.store, actor="cto") == first


def test_measurement_strata_serialize_to_json(measure_store) -> None:
    import json

    store = measure_store.store
    skill = "workspace/skills/test-first.md"
    write_attempt(store, "01JUSGCAND0000000000000005", model="m1", skills=((skill, 1),))
    write_attempt(store, "01JUSGCAND0000000000000006", model="m1", skills=((skill, 2),))
    args = {"target_class": "skill", "target": skill, "before": 1, "after": 2}
    versions = invoke("measure versions", args, store=store, actor="cto")["value"]
    assert versions["strata"]["before"] == [
        {"role": "unknown", "model": "m1", "candidates": 1}
    ]
    json.dumps(versions)
    usage = invoke("measure usage", {"group_by": "model"}, store=store, actor="cto")["value"]
    json.dumps(usage)
