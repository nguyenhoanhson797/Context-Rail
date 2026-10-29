"""The ledger: attempt-counted usage with unknown counters kept apart."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.cto import USAGE_KEYS
from helpers import run_cli, write_run


def _ledger(store, **params: object) -> dict:
    result = invoke("ledger", params, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_ledger_keeps_unknown_separate(unknown_usage_run: SimpleNamespace) -> None:
    value = _ledger(unknown_usage_run.store, item=unknown_usage_run.item)
    assert set(value) == {"runs", "totals", "unknown_counts", "total", "omitted"}
    row = value["runs"][0]
    assert set(USAGE_KEYS) <= set(row["usage"])
    assert {counter: row["usage"][counter] for counter in USAGE_KEYS} == dict.fromkeys(
        USAGE_KEYS, "unknown"
    )
    assert value["totals"]["unknown_attempts"]["factory_credits"] == 1
    assert "factory_credits" not in value["totals"]["known_sum"]
    assert value["unknown_counts"]["factory_credits"] == 1
    assert row["duration_s"] == 1.0 and row["attempt_count"] == 1
    assert row["profile"] == "implementer" and row["model"]


def test_ledger_totals_never_present_an_unknown_counter_as_zero(
    unknown_usage_run: SimpleNamespace,
) -> None:
    value = _ledger(unknown_usage_run.store)
    for counter in USAGE_KEYS:
        assert value["unknown_counts"][counter] >= 1
        assert value["totals"]["known_sum"].get(counter, "absent") != 0


def test_ledger_does_not_double_count_tick_and_worker_usage(
    tick_with_workers: SimpleNamespace,
) -> None:
    value = _ledger(tick_with_workers.store)
    assert value["total"] == 2
    assert value["totals"]["known_sum"]["input_tokens"] == (
        tick_with_workers.worker_input + tick_with_workers.tick_input
    )
    assert value["totals"]["known_sum"]["output_tokens"] == 5 + 2
    assert "input_tokens" not in value["unknown_counts"]
    assert "output_tokens" not in value["unknown_counts"]
    assert value["unknown_counts"]["cache_read_input_tokens"] == 2
    roles = [row["role"] for row in value["runs"]]
    assert sorted(roles) == ["cto", "implementer"]
    tick = [row for row in value["runs"] if row["role"] == "cto"][0]
    assert tick["item"] is None
    assert tick["usage"]["input_tokens"] == tick_with_workers.tick_input


def test_ledger_bounds_the_rows_and_reports_the_omissions(tmp_path) -> None:
    from perf.gen import make_store

    store = make_store(tmp_path / "ledger", 6)
    for index in range(1, 4):
        write_run(store, f"01JLEDGERRUN0000000000000{index}", item="t0", dispatch=f"d{index}")
    value = _ledger(store, limit=2)
    assert value["total"] == 3
    assert len(value["runs"]) == 2
    assert value["omitted"] == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"since": "yesterday"}])
def test_ledger_refuses_out_of_range_filters(tmp_path, params) -> None:
    from perf.gen import make_store

    store = make_store(tmp_path / "ledger-refused", 6)
    result = invoke("ledger", params, store=store, actor="cto")
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_cli_ledger_prints_the_bounded_envelope(
    unknown_usage_run: SimpleNamespace,
) -> None:
    completed = run_cli(
        unknown_usage_run.store.parent,
        "ledger",
        "--limit",
        "20",
        store=unknown_usage_run.store,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)["value"]
    assert value["totals"]["unknown_attempts"]["factory_credits"] == 1
    assert value["runs"][0]["usage"]["factory_credits"] == "unknown"


def test_cli_ledger_exit_code_for_a_refused_filter(unknown_usage_run: SimpleNamespace) -> None:
    completed = run_cli(
        unknown_usage_run.store.parent,
        "ledger",
        "--limit",
        "0",
        store=unknown_usage_run.store,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["error"]["code"] == "VALIDATION_FAILED"
