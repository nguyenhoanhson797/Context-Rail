"""The legacy read envelopes, composed from the current engine.

The React pages ask for named reads (`next`, `scope_score`, `understanding`,
`doctor`) and expect ``{status: 'ok' | 'error', ...}``. Only the reads a phase
one screen uses are implemented; every other name is an explicit refusal, never
an empty success.
"""

from __future__ import annotations

import os
from typing import Any

from context_rail.store import query

__all__ = ["read"]


def read(context: Any, view: str) -> dict[str, Any]:
    """One named read, or an explicit error envelope."""
    handlers = {
        "next": _next,
        "scope_score": _scope_score,
        "understanding": _understanding,
        "doctor": _doctor,
        "config_options": _config_options,
    }
    handler = handlers.get(view)
    if handler is None:
        return {
            "status": "error",
            "error": f"the {view!r} read is not part of this UI's phase",
        }
    try:
        return handler(context)
    except Exception as error:  # a read failure is an envelope, not a crash
        return {"status": "error", "error": str(error) or error.__class__.__name__}


def _next(context: Any) -> dict[str, Any]:
    from context_rail.context import operating

    model = getattr(context, "model", None)
    if model is None:
        from context_rail.model.compiler import compile_project

        model = compile_project(context.pack, context.pack.operating)
    try:
        view = operating.resolve_queue(context, model, context.actor)
    except operating.OperatingError as error:
        return {
            "status": "error",
            "error": error.message,
            "code": error.code,
            "candidates": error.details.get("candidates", []),
        }
    queue = [
        {
            "id": item.slug,
            "title": item.title,
            "workflow": item.workflow,
            "status": item.status,
            "category": context.pack.statuses.get(item.status, ""),
            "focus": item.focus,
            "actions": list(item.actions),
            "why": item.why,
        }
        for item in view.items
    ]
    return {
        "status": "ok",
        "queue": queue,
        "count": len(queue),
        "focus": view.focus.as_dict(),
        "scope": view.scope,
    }


def _scope_items(context: Any) -> list[dict[str, Any]]:
    rows = context.index.conn.execute(
        "SELECT d.slug, d.title FROM docs d WHERE d.primitive = 'item' "
        "AND d.broken = 0 ORDER BY d.slug"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for slug, title in rows:
        preset = context.pack.presets.get(
            context.index.conn.execute(
                "SELECT preset FROM docs WHERE slug = ? ORDER BY path LIMIT 1",
                (slug,),
            ).fetchone()[0]
            or ""
        )
        if preset is None or "priority" not in preset.fields:
            continue
        checks = query.check_states(context.index, slug)
        passed = [row for row in checks if row.get("passed") is True]
        fresh = [
            row
            for row in checks
            if row.get("passed") is True
            and row.get("check_etag") == row.get("definition_etag")
        ]
        total = len(checks)
        out.append(
            {
                "id": slug,
                "title": title,
                "has_checklist": bool(total),
                "proven": len(fresh) / total if total else None,
                "score": len(passed) / total if total else None,
                "checks": [
                    {
                        "checked": row.get("passed") is not None,
                        "state": "pass"
                        if row.get("passed") is True
                        else "fail"
                        if row.get("passed") is False
                        else "pending",
                    }
                    for row in checks
                ],
            }
        )
    return out


def _scope_score(context: Any) -> dict[str, Any]:
    items = _scope_items(context)
    total = len(items)
    proven = (
        sum(1 for item in items if item["proven"] == 1) / total if total else None
    )
    checked = (
        sum(1 for item in items if item["score"] == 1) / total if total else None
    )
    return {
        "status": "ok",
        "items": items,
        "totals": {
            "final_score": proven,
            "checked_score": checked,
            "items": total,
        },
        "summary": (
            f"{sum(1 for item in items if item['proven'] == 1)} of {total} scope "
            "items fully proven"
            if total
            else "no scope item carries a checklist"
        ),
    }


def _understanding(context: Any) -> dict[str, Any]:
    """Phase one has no goal-to-work model yet; the pages render no outcomes."""
    return {"status": "ok", "goals": [], "outcomes": []}


def _doctor(context: Any) -> dict[str, Any]:
    from context_rail.doctor.checks import run_doctor

    report = run_doctor(
        context.store, context.manifest, context.pack, context.index
    )
    issues = [
        {
            "check": issue["code"],
            "severity": issue["severity"],
            "message": issue["message"],
            "file": issue.get("path"),
            "field": None,
            "fix_hint": issue.get("fix"),
        }
        for issue in report["issues"]
    ]
    return {
        "status": "ok",
        "issues": issues,
        "counts": report["counts"],
    }


def _config_options(context: Any) -> dict[str, Any]:
    """The engine's profile vocabularies, so a form offers choices.

    These are engine facts, not pack facts: the same adapters and tool ids
    exist for every project, and every harness is one of the runners.
    """
    from context_rail.model.cto import (
        PROFILE_AUTO,
        PROFILE_EFFORTS,
        PROFILE_METHODS,
        PROFILE_RUNNERS,
        TOOL_IDS,
    )

    return {
        "status": "ok",
        "runners": list(PROFILE_RUNNERS),
        "efforts": [value for value in PROFILE_EFFORTS if value is not None],
        "autos": [value for value in PROFILE_AUTO if value is not None],
        "tools": list(TOOL_IDS),
        "methods": list(PROFILE_METHODS),
        "runner_override": os.environ.get("RAIL_RUNNER") or None,
    }
