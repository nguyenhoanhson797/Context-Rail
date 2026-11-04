"""Typed Result: failures are values, never thrown across a boundary.

Every value that crosses a tool boundary is either ``{"ok": True, "value":
...}`` or ``{"ok": False, "error": {"code": ..., "message": ...}}``, so callers
branch on data instead of on exceptions.
"""

from __future__ import annotations

from typing import Any

ERROR_CODES = (
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
)

#: Codes for something that broke rather than something refused: the CLI exits
#: with 2 for these and 1 for every other failure code.
INFRA_CODES = ("INTERNAL", "INTEGRATION_FAILURE", "PROCESS_ERROR")


def ok(value: Any) -> dict:
    """Success value."""
    return {"ok": True, "value": value}


def fail(
    code: str,
    message: str,
    details: dict | None = None,
    process: str | None = None,
) -> dict:
    """Typed failure. Unknown codes are rejected loudly (fail fast)."""
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code: {code!r}")
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    if process:
        error["process"] = process
    return {"ok": False, "error": error}


def from_exception(exc: BaseException, code: str = "INTERNAL") -> dict:
    """Wrap a thrown exception so infrastructure errors never escape raw.

    The traceback stays server-side (stderr); only the message crosses the
    boundary.
    """
    message = str(exc) if str(exc) else exc.__class__.__name__
    return fail(code, message)


def is_ok(result: dict) -> bool:
    """Narrowing helper — usable without exceptions."""
    return result.get("ok") is True
