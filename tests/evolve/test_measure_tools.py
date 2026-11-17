from __future__ import annotations

import hashlib

import pytest

from context_rail.gateway.gateway import invoke
from helpers import record_result_for_test, write_attempt


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


def test_measure_usage_refuses_an_unknown_group_by(measure_store) -> None:
    result = invoke(
        "measure usage", {"group_by": "model_version"}, store=measure_store.store, actor="cto"
    )
    assert result["ok"] is False, result
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert result["error"]["details"]["allowed"] == [
        "item", "model", "harness", "role", "profile", "skill"
    ]


@pytest.mark.parametrize("limit", [0, 101])
def test_measure_usage_refuses_a_limit_out_of_bounds(measure_store, limit) -> None:
    result = invoke(
        "measure usage", {"group_by": "item", "limit": limit}, store=measure_store.store, actor="cto"
    )
    assert result["ok"] is False, result
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_an_empty_window_reports_no_rows_and_no_usage(measure_store) -> None:
    result = invoke("measure usage", {"group_by": "model"}, store=measure_store.store, actor="cto")
    assert result["ok"] is True, result
    value = result["value"]
    assert value["rows"] == [] and value["omitted"] == 0
    assert value["total"]["candidates"] == 0
    assert value["total"]["usage"] == {}
    assert value["total"]["duration"] is None


def test_usage_rows_are_bounded_and_the_evidence_is_capped(measure_store) -> None:
    store = measure_store.store
    for number in range(51):
        write_attempt(
            store, f"cand-usage-cap-{number:02d}", model=f"m{number % 3}", usage={"tokens": 1}
        )
    value = invoke(
        "measure usage", {"group_by": "model", "limit": 2}, store=store, actor="cto"
    )["value"]
    assert len(value["rows"]) == 2 and value["omitted"] == 1
    assert value["total"]["candidates"] == 51
    assert value["total"]["usage"]["tokens"]["total"] == 51
    assert len(value["evidence"]["candidates"]) == 50
    assert value["evidence"]["omitted_count"] == 1


def test_commitless_work_appears_in_the_usage_rollup(effort_store) -> None:
    record_result_for_test(
        effort_store.store,
        effort_store.task,
        effort={"duration_s": 30, "usage": {"tokens": 5}},
        model="research-model",
    )
    value = invoke(
        "measure usage", {"group_by": "item"}, store=effort_store.store, actor="cto"
    )["value"]
    assert [row["key"] for row in value["rows"]] == [effort_store.task]
    assert value["rows"][0]["usage"]["tokens"]["total"] == 5
    assert value["rows"][0]["pending"] == 1
    assert value["rows"][0]["duration"] == "30"
    assert value["total"]["candidates"] == 1


def test_the_digest_usage_includes_commitless_work(effort_store) -> None:
    record_result_for_test(
        effort_store.store, effort_store.task, effort={"usage": {"tokens": 5}}
    )
    digest = invoke("digest", {}, store=effort_store.store, actor="cto")["value"]
    assert digest["usage"]["known_sum"]["tokens"] == 5


def test_the_ledger_stays_the_candidate_trail(effort_store) -> None:
    record_result_for_test(
        effort_store.store, effort_store.task, effort={"usage": {"tokens": 5}}
    )
    ledger = invoke("ledger", {"limit": 20}, store=effort_store.store, actor="cto")["value"]
    assert ledger["candidates"] == []


def test_a_bound_result_does_not_double_count_in_the_rollup(effort_store) -> None:
    store, task = effort_store.store, effort_store.task
    write_attempt(
        store, "cand-join-1", item=task, commit="1" * 40, actor="implementer", usage={"tokens": 10}
    )
    record_result_for_test(store, task, commit="1" * 40, effort={"usage": {"tokens": 99}})
    value = invoke("measure usage", {"group_by": "item"}, store=store, actor="cto")["value"]
    assert value["total"]["candidates"] == 1
    assert value["total"]["usage"]["tokens"]["total"] == 99


def test_rebuilt_index_returns_identical_joined_usage(effort_store) -> None:
    record_result_for_test(
        effort_store.store, effort_store.task, effort={"usage": {"tokens": 5}}
    )
    args = {"group_by": "item"}
    first = invoke("measure usage", args, store=effort_store.store, actor="cto")
    effort_store.ix.close()
    (effort_store.store / ".rail-local").mkdir(exist_ok=True)
    invoke("reindex", {"full": True}, store=effort_store.store, actor="cto")
    assert invoke("measure usage", args, store=effort_store.store, actor="cto") == first
