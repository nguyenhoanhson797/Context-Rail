"""A result that reports no effort is a warning, never a block."""

from __future__ import annotations

from context_rail.gateway.gateway import invoke
from helpers import record_result_for_test


def _issues(store, code: str = "effort_unreported") -> list[dict]:
    result = invoke("doctor", {}, store=store, actor="cto")
    assert result["ok"], result
    return [issue for issue in result["value"]["issues"] if issue["code"] == code]


def test_no_result_means_no_effort_warning(effort_store) -> None:
    assert _issues(effort_store.store) == []


def test_a_reported_counter_is_not_a_warning(effort_store) -> None:
    record_result_for_test(
        effort_store.store, effort_store.task, effort={"usage": {"tokens": 10}}
    )
    assert _issues(effort_store.store) == []


def test_a_partially_unknown_map_is_not_a_warning(effort_store) -> None:
    record_result_for_test(
        effort_store.store,
        effort_store.task,
        effort={"usage": {"tokens": "unknown", "requests": 5}},
    )
    assert _issues(effort_store.store) == []


def test_an_all_unknown_map_warns(effort_store) -> None:
    record_result_for_test(
        effort_store.store, effort_store.task, effort={"usage": {"tokens": "unknown"}}
    )
    assert len(_issues(effort_store.store)) == 1


def test_silence_warns_once_and_never_blocks_the_gate(effort_store) -> None:
    record_result_for_test(effort_store.store, effort_store.task)
    issues = _issues(effort_store.store)
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["fix"]

    gate = invoke("ci", {}, store=effort_store.store, actor="cto")
    assert gate["ok"], gate
    assert gate["value"]["counts"]["warning"] >= 1
    assert gate["value"]["counts"]["error"] == 0
