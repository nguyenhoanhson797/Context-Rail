"""Changed symbols on the run, and the code impact the reviewer reads."""

from __future__ import annotations

import pytest
from code_fixtures import APP, CART_TEST, dispatch, run_payload, snapshot_of, with_body
from helpers import latest_record, run_git

from context_rail.gateway.gateway import invoke
from context_rail.gateway.handlers.dispatch import code_area
from context_rail.model.cto import SYMBOLS_CHANGED_MAX, parse_run
from context_rail.model.errors import ModelError
from context_rail.runner import dispatch as local_dispatch
from context_rail.store.codegraph import cache_path, has_snapshot, save_snapshot

TOTAL_EDIT = APP.replace("sum(items)", "sum(i for i in items if i)")
BODY = "Make `src/app.py:total` ignore None."


def _review(leaf, run_id: str, key: str = "rv"):
    return invoke(
        "review",
        {"item": leaf.slug, "run": run_id, "key": key},
        store=leaf.store,
        actor="cto",
    )


def _first_call_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The base snapshot builds, then vanishes before judging."""
    real = code_area.open_graph
    calls = {"count": 0}

    def flaky(store, commit):
        calls["count"] += 1
        return real(store, commit) if calls["count"] == 1 else None

    monkeypatch.setattr(code_area, "open_graph", flaky)


def test_a_changed_function_is_recorded(leaf_item, fake_droid, droid_env) -> None:
    leaf = with_body(leaf_item, BODY)
    fake_droid.set_behaviors([{"mode": "success", "edits": {"src/app.py": TOTAL_EDIT}}])

    result = dispatch(leaf, "changed")

    assert result["ok"], result
    data = latest_record(leaf.store, "run.completed")["data"]
    assert data["symbols_changed"] == ["fake-droid-work.txt", "src/app.py:total"]


def test_a_new_file_is_recorded_at_file_level(leaf_item, fake_droid, droid_env) -> None:
    leaf = with_body(leaf_item, BODY)
    fake_droid.set_behaviors([{"mode": "success", "edits": {"src/new.py": "VALUE = 1\n"}}])

    dispatch(leaf, "new-file")

    data = latest_record(leaf.store, "run.completed")["data"]
    assert "src/new.py" in data["symbols_changed"]


def test_a_missing_base_snapshot_leaves_the_key_absent(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = with_body(leaf_item, BODY)
    _first_call_only(monkeypatch)

    result = dispatch(leaf, "no-base")

    assert result["ok"], result
    assert result["value"]["status"] == "verified"
    assert "symbols_changed" not in latest_record(leaf.store, "run.completed")["data"]


def test_a_truncated_capture_leaves_the_key_absent(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = with_body(leaf_item, BODY)
    monkeypatch.setattr(
        code_area.git_runner,
        "changed_line_ranges",
        lambda **kwargs: {"files": {}, "complete": False},
    )

    result = dispatch(leaf, "cut")

    assert result["ok"], result
    assert "symbols_changed" not in latest_record(leaf.store, "run.completed")["data"]


def test_code_graph_none_records_no_symbols(leaf_item, fake_droid, droid_env, monkeypatch) -> None:
    leaf = with_body(leaf_item, BODY)
    monkeypatch.setattr(code_area, "configured_graph", lambda ctx: "none")

    dispatch(leaf, "off")

    data = latest_record(leaf.store, "run.completed")["data"]
    assert "symbols_changed" not in data and "code_area" not in data


def test_the_review_order_shows_impact_and_scope(leaf_item, fake_droid, droid_env) -> None:
    leaf = with_body(leaf_item, BODY, {"tests/test_cart.py": CART_TEST})
    fake_droid.set_behaviors([{"mode": "success", "edits": {"src/app.py": TOTAL_EDIT}}])
    dispatched = dispatch(leaf, "impact")
    assert dispatched["ok"], dispatched
    fake_droid.set_behaviors([{"mode": "review_approve", "review": {"evidence": []}}])

    reviewed = _review(leaf, dispatched["value"]["run"])

    assert reviewed["ok"], reviewed
    order = fake_droid.log()[-1]["prompt"]
    section = order.split("## code area", 1)[1]
    lines = {line.split(":", 1)[0]: line for line in section.splitlines() if ":" in line}
    assert "src/app.py:total" in lines["changed"]
    assert "src/app.py:checkout" in lines["impact"]
    assert "lib/cart.py:pay" in lines["impact"]
    assert "fake-droid-work.txt" in lines["outside predicted area"]
    assert "tests/test_cart.py:test_pay" in lines["tests reaching the change, not declared as checks"]


def test_the_review_says_why_when_symbols_are_unknown(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = with_body(leaf_item, BODY)
    _first_call_only(monkeypatch)
    dispatched = dispatch(leaf, "unknown")
    assert dispatched["ok"], dispatched
    cache_path(leaf.store).unlink()
    fake_droid.set_behaviors([{"mode": "review_approve", "review": {"evidence": []}}])

    reviewed = _review(leaf, dispatched["value"]["run"])

    assert reviewed["ok"], reviewed
    section = fake_droid.log()[-1]["prompt"].split("## code area", 1)[1]
    assert "file-level only: no base snapshot" in section


def test_a_protected_base_survives_retention_until_judged(
    leaf_item, fake_droid, droid_env
) -> None:
    leaf = with_body(leaf_item, BODY)
    base = run_git(leaf.root, "rev-parse", "HEAD").stdout.strip()
    save_snapshot(leaf.store, snapshot_of(base))
    local_dispatch.write_progress(
        leaf.store, "inflight", item="other", state="running", base_commit=base
    )

    protect = [entry["base_commit"] for entry in local_dispatch.active_dispatches(leaf.store)]
    for number in range(4):
        save_snapshot(leaf.store, snapshot_of(f"other{number}"), protect=protect)

    assert has_snapshot(leaf.store, base)


def test_parse_run_bounds_symbols_changed() -> None:
    parse_run(run_payload(symbols_changed=[f"f{n}.py" for n in range(SYMBOLS_CHANGED_MAX)]))
    with pytest.raises(ModelError) as refused:
        parse_run(run_payload(symbols_changed=[f"f{n}.py" for n in range(SYMBOLS_CHANGED_MAX + 1)]))
    assert refused.value.code == "VALIDATION_FAILED"
