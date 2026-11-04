"""Strict payloads for generic execution records.

An execution record is what an external action (a render, a generate, a batch)
reports. It is not a workflow status: the engine never maps one onto the other.
Worker ``run.completed`` records stay their own schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from context_rail.core.util import canonical_iso
from context_rail.model.fields import (
    integer,
    mapping,
    no_unknown,
    refuse,
    require,
    schema_version,
    string,
    string_list,
)

__all__ = ["EXECUTION_KIND", "EXECUTION_STATES", "ExecutionRecord", "parse_execution"]

EXECUTION_KIND = "execution.completed"
EXECUTION_STATES = ("running", "succeeded", "failed", "cancelled")
EXECUTION_KEYS = (
    "schema",
    "action",
    "target",
    "state",
    "attempt",
    "started_at",
    "completed_at",
    "outputs",
    "error",
    "actor",
    "tool",
    "params",
    "runner_digest",
)
_EXECUTION_REQUIRED = (
    "action",
    "target",
    "state",
    "attempt",
    "started_at",
    "actor",
    "tool",
)


@dataclass(frozen=True)
class ExecutionRecord:
    """One immutable attempt of one action on one target."""

    action: str
    target: str
    state: str
    attempt: int
    started_at: str
    completed_at: str | None
    outputs: tuple[str, ...]
    error: str | None
    actor: str
    tool: str
    params: dict[str, Any]
    runner_digest: str | None


def parse_execution(data: Any) -> ExecutionRecord:
    """Validate one ``execution.completed`` payload, refusing every unknown key."""
    payload = mapping(data, "execution")
    schema_version(payload, 1, "execution")
    no_unknown(payload, EXECUTION_KEYS, "execution")
    require(payload, _EXECUTION_REQUIRED, "execution")

    action = string(payload["action"], "execution.action")
    target = string(payload["target"], "execution.target")
    state = string(payload["state"], "execution.state")
    if state not in EXECUTION_STATES:
        refuse("execution.state must be one of " + ", ".join(EXECUTION_STATES))
    attempt = integer(payload["attempt"], "execution.attempt", positive=True)
    started = _timestamp(payload["started_at"], "execution.started_at")
    completed = payload.get("completed_at")
    completed_at = (
        None if completed is None else _timestamp(completed, "execution.completed_at")
    )
    outputs = string_list(
        payload.get("outputs", []), "execution.outputs", allow_empty=True
    )
    error = payload.get("error")
    if error is not None:
        if not isinstance(error, str):
            refuse("execution.error must be text or null")
        if not error.strip():
            refuse("execution.error must not be empty when given")
    actor = string(payload["actor"], "execution.actor")
    tool = string(payload["tool"], "execution.tool")
    params = mapping(payload.get("params", {}), "execution.params")
    for key, value in params.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            refuse(f"execution.params.{key} must be JSON")
    digest = payload.get("runner_digest")
    if digest is not None:
        digest = string(digest, "execution.runner_digest")
    return ExecutionRecord(
        action=action,
        target=target,
        state=state,
        attempt=attempt,
        started_at=started,
        completed_at=completed_at,
        outputs=outputs,
        error=error,
        actor=actor,
        tool=tool,
        params=dict(params),
        runner_digest=digest,
    )


def _timestamp(raw: Any, what: str) -> str:
    value = canonical_iso(string(raw, what))
    if value is None:
        refuse(f"{what} must be an ISO 8601 timestamp")
    return value
