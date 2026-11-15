"""Strict Check and Verdict validation for explicit ``mode: result`` checks."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.cto import (
    CHECK_MODES,
    MODE_ONLY_KEYS,
    RESULT_BINDING_KEYS,
    parse_check,
    parse_verdict,
)
from context_rail.model.errors import ModelError


def _result_verdict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "check": "result-check",
        "check_etag": "a" * 16,
        "passed": True,
        "mode": "result",
        "input_blobs": {},
        "input_globs": [],
        "runner_digest": None,
        "exit": None,
        "evidence": ["01JRES00000000000000000001"],
        "reason": None,
        "candidate_commit": None,
        "result": {
            "record": "01JRES00000000000000000001",
            "body_hash": "b" * 64,
            "item_digest": "c" * 64,
            "work_cycle": None,
            "contract_digest": "d" * 64,
            "candidate_commit": None,
        },
    }
    base.update(over)
    return base


def test_result_mode_check_parses_with_no_extra_mode_fields() -> None:
    assert "result" in CHECK_MODES
    assert MODE_ONLY_KEYS["result"] == frozenset()
    assert RESULT_BINDING_KEYS == (
        "record",
        "body_hash",
        "item_digest",
        "work_cycle",
        "contract_digest",
        "candidate_commit",
    )
    parsed = parse_check({"schema": 1, "mode": "result", "acceptance": False})
    assert parsed.mode == "result"
    assert parsed.acceptance is False

    accept = parse_check(
        {"schema": 1, "mode": "result", "acceptance": True, "verifies": ["task-1"]}
    )
    assert accept.mode == "result"
    assert accept.verifies == ("task-1",)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"cmd": ["py", "-c", "0"]}, id="cmd"),
        pytest.param({"inputs": ["src/**/*.py"]}, id="inputs"),
        pytest.param({"exists": ["README.md"]}, id="exists"),
        pytest.param({"evidence": ["01J00000000000000000000003"]}, id="evidence"),
        pytest.param({"store": "../child"}, id="store"),
        pytest.param({"item": "task-1"}, id="item"),
        pytest.param({"category": "done"}, id="category"),
        pytest.param({"unknown_field": True}, id="unknown"),
    ],
)
def test_result_mode_refuses_incompatible_and_unknown_fields(
    extra: dict[str, Any],
) -> None:
    payload = {"schema": 1, "mode": "result", "acceptance": False, **extra}
    with pytest.raises(ModelError) as excinfo:
        parse_check(payload)
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_result_verdict_requires_exact_binding_and_rejects_runner_or_store() -> None:
    parsed = parse_verdict(_result_verdict())
    assert parsed.mode == "result"
    assert parsed.result is not None
    assert parsed.result["record"] == "01JRES00000000000000000001"

    failed_missing_result = parse_verdict(_result_verdict(passed=False, result=None))
    assert failed_missing_result.passed is False
    assert failed_missing_result.result is None

    with pytest.raises(ModelError):
        parse_verdict(_result_verdict(passed=True, result=None))
    with pytest.raises(ModelError):
        parse_verdict(_result_verdict(runner_digest="sha256:x"))
    with pytest.raises(ModelError):
        parse_verdict(_result_verdict(exit=0))
    with pytest.raises(ModelError):
        parse_verdict(
            _result_verdict(
                store={
                    "path": "../child",
                    "item": "t",
                    "doc": "items/task/t.md",
                    "commit": "a" * 40,
                    "etag": "0" * 16,
                    "category": "done",
                }
            )
        )
    with pytest.raises(ModelError):
        parse_verdict(
            _result_verdict(
                result={
                    "record": "01JRES00000000000000000001",
                    "body_hash": "b" * 64,
                    "item_digest": "c" * 64,
                    "work_cycle": None,
                    "contract_digest": "d" * 64,
                    "candidate_commit": None,
                    "extra": 1,
                }
            )
        )
    # Non-result verdicts may not carry result binding
    with pytest.raises(ModelError):
        parse_verdict(
            {
                "schema": 1,
                "check": "human-signoff",
                "check_etag": "a" * 16,
                "passed": True,
                "mode": "human",
                "input_blobs": {},
                "input_globs": [],
                "runner_digest": None,
                "exit": None,
                "evidence": [],
                "reason": None,
                "candidate_commit": None,
                "result": _result_verdict()["result"],
            }
        )
