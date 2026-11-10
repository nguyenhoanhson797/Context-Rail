import pytest

from context_rail.core.result import (
    ERROR_CODES,
    INFRA_CODES,
    fail,
    from_exception,
    is_ok,
    ok,
)


def test_fail_rejects_unknown_code():
    with pytest.raises(ValueError):
        fail("NOPE", "x")


def test_ok_and_fail_shapes():
    assert ok({"a": 1}) == {"ok": True, "value": {"a": 1}}
    assert fail("CONFLICT", "stale") == {
        "ok": False,
        "error": {"code": "CONFLICT", "message": "stale"},
    }


def test_fail_keeps_details_only_when_supplied():
    assert fail("CONFLICT", "stale", {"path": "a.md"})["error"]["details"] == {
        "path": "a.md"
    }
    assert "details" not in fail("CONFLICT", "stale")["error"]


def test_error_vocabulary_is_the_copied_legacy_one():
    assert set(ERROR_CODES) == {
        "VALIDATION_FAILED",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "FORBIDDEN",
        "INVALID_STATE",
        "CONFLICT",
        "BUSINESS_RULE_VIOLATED",
        "EXPRESSION_ERROR",
        "INTEGRATION_FAILURE",
        "CYCLE_DETECTED",
        "PROCESS_ERROR",
        "INTERNAL",
        "SCHEMA_UNSUPPORTED",
        "NO_FOCUS",
        "AMBIGUOUS_FOCUS",
        "INVALID_FOCUS",
        "SESSION_REQUIRED",
    }
    assert set(INFRA_CODES) == {"INTERNAL", "INTEGRATION_FAILURE", "PROCESS_ERROR"}


def test_session_required_is_a_refusal_not_an_infrastructure_error():
    from context_rail.cli import _finish

    result = fail("SESSION_REQUIRED", "x")

    assert result == {"ok": False, "error": {"code": "SESSION_REQUIRED", "message": "x"}}
    assert "SESSION_REQUIRED" not in INFRA_CODES
    assert _finish("seat list", result) == 1


def test_is_ok_narrows_both_shapes():
    assert is_ok(ok(1)) is True
    assert is_ok(fail("NOT_FOUND", "gone")) is False


def test_from_exception_wraps_as_infrastructure_result():
    assert from_exception(RuntimeError("boom")) == {
        "ok": False,
        "error": {"code": "INTERNAL", "message": "boom"},
    }
    assert from_exception(RuntimeError("boom"), "PROCESS_ERROR")["error"]["code"] == (
        "PROCESS_ERROR"
    )
