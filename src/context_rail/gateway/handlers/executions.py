"""Record one attempt of a declared execution action on its target.

The engine never runs a render, a generation or a batch. Whoever does reports
each attempt here, and the record joins the progress view and the ledger
without ever becoming a workflow status. An attempt is opened by ``running``;
a terminal state closes the open attempt, or stands as an attempt of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso
from context_rail.model.errors import ModelError
from context_rail.model.execution import (
    EXECUTION_KIND,
    EXECUTION_STATES,
    parse_execution,
)
from context_rail.store import query
from context_rail.store.lock import store_lock
from context_rail.store.write import WriteRefused, commit, replay

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["record"]

_TOOL = "execution record"


def record(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Append one ``execution.completed`` record after the pack's own checks."""
    name = args["action"]
    target = args["target"]
    state = args["state"]
    declared = ctx.model.operating.execution_actions
    if name not in declared:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"action {name!r} does not report execution; declared: "
            + (", ".join(declared) or "none"),
            {"action": name, "declared": list(declared)},
        )
    if state not in EXECUTION_STATES:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"state must be one of {', '.join(EXECUTION_STATES)}",
            {"state": state},
        )
    action = ctx.pack.action(name)
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, args["key"])
        if saved is not None:
            return _value(saved, name, target, state)
        index.sync()
        row = index.conn.execute(
            "SELECT preset, status FROM docs WHERE slug = ? ORDER BY path LIMIT 1",
            (target,),
        ).fetchone()
        if row is None:
            raise WriteRefused("NOT_FOUND", f"no item {target!r}", {"item": target})
        preset, status = row
        if action.on != "*" and preset != action.on:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"action {name!r} applies to a {action.on!r}, not a {preset!r}",
                {"action": name, "on": action.on, "preset": preset},
            )
        latest = query.execution_latest(index, actions=(name,), targets=(target,))
        open_attempt = latest[0] if latest and latest[0]["state"] == "running" else None
        now = now_iso()
        if state == "running":
            if open_attempt is not None:
                raise WriteRefused(
                    "CONFLICT",
                    f"attempt {open_attempt['attempt']} of {name!r} on {target!r} is "
                    "still running; record how it ended first",
                    {"attempt": open_attempt["attempt"]},
                )
            if action.from_states and status not in action.from_states:
                raise WriteRefused(
                    "INVALID_STATE",
                    f"action {name!r} starts from "
                    + ", ".join(action.from_states)
                    + f", not {status!r}",
                    {"action": name, "from": list(action.from_states), "status": status},
                )
        if open_attempt is not None:
            attempt, started = open_attempt["attempt"], open_attempt["started_at"]
        else:
            attempt = (latest[0]["attempt"] if latest else 0) + 1
            started = now
        payload = {
            "schema": 1,
            "action": name,
            "target": target,
            "state": state,
            "attempt": attempt,
            "started_at": started,
            "completed_at": None if state == "running" else now,
            "outputs": list(args.get("output") or []),
            "error": args.get("error"),
            "actor": actor,
            "tool": _TOOL,
            "params": {},
            "runner_digest": args.get("runner_digest"),
        }
        try:
            parse_execution(payload)
        except ModelError as error:
            raise WriteRefused("VALIDATION_FAILED", error.message) from None
        result = commit(
            store,
            ctx.manifest,
            [],
            {"kind": EXECUTION_KIND, "about": [target], "data": payload},
            actor=actor,
            key=args["key"],
            index=index,
        )
    return {
        "record": result.get("record"),
        "action": name,
        "target": target,
        "state": state,
        "attempt": attempt,
    }


def _value(saved: dict[str, Any], name: str, target: str, state: str) -> dict[str, Any]:
    return {"record": saved.get("record"), "action": name, "target": target, "state": state}
