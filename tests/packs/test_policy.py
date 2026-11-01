from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.pack import effective_measurement_min_runs, load_pack
from helpers import init_store, write_pack

_BASE = {
    "name": "custom",
    "version": 1,
    "statuses": {"todo": "not_started", "doing": "active", "done": "done", "dropped": "dropped"},
    "initial": "todo",
    "transitions": {"todo": ["doing", "dropped"], "doing": ["done", "todo"], "done": ["doing"], "dropped": ["todo"]},
    "presets": {"task": {"primitive": "item", "tags": ["task"]}},
    "policy": {"stuck_hours": 24, "retries": 1},
}


def _with_policy(store: Path, policy: dict) -> Path:
    document = dict(_BASE, policy=policy)
    return write_pack(store, "custom", document)


@pytest.mark.parametrize("value", [9, True, "10"])
def test_measurement_min_runs_refuses_non_integers_and_small_values(tmp_path: Path, value: object) -> None:
    store = init_store(tmp_path)
    _with_policy(store, {"stuck_hours": 24, "retries": 1, "measurement_min_runs": value})
    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)
    assert caught.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("policy", [
    {"stuck_hours": 24, "retries": 1, "charter_budget": 5},
    {"stuck_hours": 24, "retries": 1, "measurement_min_run": 10},
])
def test_unknown_and_misspelled_policy_keys_refuse(tmp_path: Path, policy: dict) -> None:
    store = init_store(tmp_path)
    _with_policy(store, policy)
    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)
    assert caught.value.code == "VALIDATION_FAILED"


def test_accepted_values_and_default_resolution(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    _with_policy(store, {"stuck_hours": 24, "retries": 1, "measurement_min_runs": 25})
    assert effective_measurement_min_runs(load_pack("custom", store)) == 25
    _with_policy(store, {"stuck_hours": 24, "retries": 1})
    assert effective_measurement_min_runs(load_pack("custom", store)) == 10


def test_the_retry_policy_caps_implementation_attempts() -> None:
    from context_rail.gateway.handlers.dispatch.budget import _effective_budget

    assert _effective_budget({"attempts": 5}, {"attempts": 4}, 1).attempts == 2
    assert _effective_budget({}, {}, 1).attempts == 2
    assert _effective_budget({}, {"attempts": 2}, 0).attempts == 1


def test_the_shipped_retry_policy_keeps_the_implementer_budget(tmp_path: Path) -> None:
    from context_rail.gateway.handlers.dispatch.budget import _effective_budget

    pack = load_pack("basic", tmp_path)
    assert pack.policy["retries"] == 1
    assert _effective_budget({}, {"attempts": 2}, pack.policy["retries"]).attempts == 2


def test_version_two_is_schema_unsupported(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    write_pack(store, "custom", dict(_BASE, version=2))
    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)
    assert caught.value.code == "SCHEMA_UNSUPPORTED"


def test_unknown_top_level_key_refuses(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    write_pack(store, "custom", dict(_BASE, extra=1))
    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)
    assert caught.value.code == "VALIDATION_FAILED"


def test_transition_to_undeclared_status_names_its_field_path(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    write_pack(store, "custom", dict(_BASE, transitions={"todo": ["nonexistent"]}))
    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)
    assert caught.value.code == "VALIDATION_FAILED"
    assert "transitions.todo" in caught.value.message


@pytest.mark.parametrize("name", ["../x", "a/b"])
def test_unsafe_pack_names_refuse(tmp_path: Path, name: str) -> None:
    store = init_store(tmp_path)
    with pytest.raises(ModelError) as caught:
        load_pack(name, store)
    assert caught.value.code == "VALIDATION_FAILED"
