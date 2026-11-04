"""Generic execution records: strict payloads, never mapped onto status."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.execution import EXECUTION_STATES, parse_execution

_VALID: dict[str, Any] = {
    "schema": 1,
    "action": "render",
    "target": "shot-3",
    "state": "succeeded",
    "attempt": 1,
    "started_at": "2026-11-04T10:00:00Z",
    "completed_at": "2026-11-04T10:04:12Z",
    "outputs": ["renders/shot-3.mp4"],
    "error": None,
    "actor": "renderer",
    "tool": "runner",
    "params": {"quality": "hero"},
    "runner_digest": "sha256:abc",
}


def _payload(**mutations: Any) -> dict[str, Any]:
    payload = copy.deepcopy(_VALID)
    for key, value in mutations.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def test_a_valid_execution_record_parses() -> None:
    record = parse_execution(_payload())

    assert record.action == "render"
    assert record.target == "shot-3"
    assert record.state == "succeeded"
    assert record.attempt == 1
    assert record.outputs == ("renders/shot-3.mp4",)
    assert record.completed_at == "2026-11-04T10:04:12Z"
    assert record.params == {"quality": "hero"}


def test_every_execution_state_is_known() -> None:
    assert set(EXECUTION_STATES) == {
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


@pytest.mark.parametrize(
    "payload",
    [
        _payload(mood="happy"),
        _payload(state="done"),
        _payload(attempt=0),
        _payload(target=None),
        _payload(started_at="yesterday"),
        _payload(completed_at="yesterday"),
        _payload(error=""),
        _payload(outputs="one.mp4"),
        _payload(params={"bad": {1, 2}}),
        _payload(schema=2),
    ],
)
def test_an_invalid_execution_record_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(ModelError) as caught:
        parse_execution(payload)

    assert caught.value.code in ("VALIDATION_FAILED", "SCHEMA_UNSUPPORTED")
