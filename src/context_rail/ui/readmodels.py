"""The legacy read envelopes, composed from the current engine.

The React pages ask for named reads (`next`, `scope_score`, `understanding`,
`doctor`) and expect ``{status: 'ok' | 'error', ...}``. Only the reads a phase
one screen uses are implemented; every other name is an explicit refusal, never
an empty success.
"""

from __future__ import annotations

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
    from context_rail.context import results as result_context_mod

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
    results_map = getattr(model, "results", {}) or {}
    contract_slugs = [item.slug for item in view.items if item.workflow in results_map]
    summaries_by_item: dict[str, dict[str, Any]] = {}
    exempt_items: set[str] = set()
    if contract_slugs:
        for summary in result_context_mod.result_summaries(
            context, contract_slugs, limit=len(contract_slugs)
        ):
            slug = summary.get("item")
            if isinstance(slug, str) and slug not in summaries_by_item:
                summaries_by_item[slug] = summary
        missing_result_slugs = [
            slug for slug in contract_slugs if slug not in summaries_by_item
        ]
        exempt_items = _batched_historical_exempt(context, missing_result_slugs)

    queue = []
    for item in view.items:
        entry: dict[str, Any] = {
            "id": item.slug,
            "title": item.title,
            "workflow": item.workflow,
            "status": item.status,
            "category": context.pack.statuses.get(item.status, ""),
            "focus": item.focus,
            "actions": list(item.actions),
            "why": item.why,
        }
        if item.workflow in results_map:
            if item.slug in exempt_items:
                entry["result"] = {
                    "required": False,
                    "record": None,
                    "current": False,
                    "completed": False,
                    "reasons": [],
                }
            else:
                summary = summaries_by_item.get(item.slug)
                if summary is None:
                    entry["result"] = {
                        "required": True,
                        "record": None,
                        "current": False,
                        "completed": False,
                        "reasons": [
                            f"no producer result has been recorded for {item.slug!r}"
                        ],
                    }
                else:
                    valid = bool(summary.get("valid", False))
                    outcome = summary.get("outcome")
                    completed = valid and outcome == "completed"
                    reasons: list[str] = []
                    if not valid:
                        reasons.append(
                            str(summary.get("problem") or "result record payload is invalid")
                        )
                    elif not completed:
                        reasons.append(
                            f"newest result outcome is {outcome!r}, not 'completed'"
                        )
                    entry["result"] = {
                        "required": True,
                        "record": summary.get("record_id"),
                        "current": valid,
                        "completed": completed,
                        "reasons": reasons,
                    }
        queue.append(entry)
    return {
        "status": "ok",
        "queue": queue,
        "count": len(queue),
        "focus": view.focus.as_dict(),
        "scope": view.scope,
    }


def _batched_historical_exempt(context: Any, slugs: list[str]) -> set[str]:
    """Check historical adoption exemptions using only indexed SQL rows (no disk reads)."""
    if not slugs:
        return set()
    pack_name = getattr(context.pack, "name", None)
    if not isinstance(pack_name, str) or not pack_name:
        return set()
    activated = query.result_activated_workflows(context.index, pack_name)
    if not activated:
        return set()
    exempt: set[str] = set()
    for start in range(0, len(slugs), 200):
        chunk = slugs[start : start + 200]
        marks = ",".join("?" for _ in chunk)
        rows = context.index.conn.execute(
            "SELECT slug, workflow, category, digest FROM docs "
            f"WHERE slug IN ({marks}) AND primitive = 'item' AND broken = 0",
            chunk,
        ).fetchall()
        done_by_slug: dict[str, tuple[str, str | None]] = {}
        for slug, workflow, category, digest in rows:
            wf = workflow or "default"
            if category == "done" and wf in activated and isinstance(digest, str):
                done_by_slug[slug] = (wf, digest)
        if not done_by_slug:
            continue
        adoptions = context.index.conn.execute(
            "SELECT item, workflow, item_digest FROM result_adoptions "
            f"WHERE pack = ? AND item IN ({','.join('?' for _ in done_by_slug)}) "
            "ORDER BY at ASC, record_id ASC",
            [pack_name, *done_by_slug],
        ).fetchall()
        adopted_digest: dict[tuple[str, str], str] = {}
        for item_slug, wf, item_digest in adoptions:
            adopted_digest.setdefault((item_slug, wf), item_digest)
        for slug, (wf, digest) in done_by_slug.items():
            if adopted_digest.get((slug, wf)) == digest:
                exempt.add(slug)
    return exempt


def _scope_items(context: Any) -> list[dict[str, Any]]:
    rows = context.index.conn.execute(
        "SELECT d.slug, d.title, d.path, d.preset FROM docs d WHERE d.primitive = 'item' "
        "AND d.broken = 0 ORDER BY d.slug"
    ).fetchall()
    reach = getattr(context, "reach", None)
    out: list[dict[str, Any]] = []
    for slug, title, path, preset_name in rows:
        if reach is not None and not reach.can_read(slug, path=path, preset=preset_name):
            continue
        preset = context.pack.presets.get(preset_name or "")
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
    """The engine's profile vocabulary, so a form offers choices.

    These are engine facts, not pack facts: the methods a profile may allow are
    the same for every project.
    """
    from context_rail.model.cto import PROFILE_METHODS

    return {"status": "ok", "methods": list(PROFILE_METHODS)}
