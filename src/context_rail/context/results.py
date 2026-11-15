"""Bounded, reach-filtered result context, summaries and contract rendering.

Reads use the index plus at most one bounded store-file read for the
selected producer result. Historical rows come from batched SQL summaries and
never open their files. Item read reach governs visibility: a seat never sees a
result ID, path or body belonging to an item outside its reach.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.store import query
from context_rail.store.doc import parse_doc
from context_rail.store.location import PathEscape, confine
from context_rail.store.results import select_result

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.model.compiler import CompiledArtifact, CompiledResults

__all__ = [
    "PINNED_RESULT_PREFIXES",
    "RESULT_BODY_BRIEF_CHARS",
    "RESULT_HISTORY_LIMIT",
    "RESULT_SUMMARY_LIMIT",
    "compiled_artifact_dict",
    "compiled_results_for_item",
    "item_in_reach",
    "render_result_details",
    "render_result_pointer_line",
    "render_result_summaries",
    "render_worker_contract",
    "result_context",
    "result_summaries",
]

PINNED_RESULT_PREFIXES = ("result:", "binding:", "latest review:")
RESULT_BODY_BRIEF_CHARS = 4000
RESULT_HISTORY_LIMIT = 5
RESULT_SUMMARY_LIMIT = 20
_SQL_BATCH = 200


def item_in_reach(ctx: Context, item: str) -> bool:
    """Whether ``item`` itself is inside the caller's read reach."""
    reach = getattr(ctx, "reach", None)
    if reach is None:
        return True
    if not isinstance(item, str) or not item:
        return False
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ? AND primitive = 'item' AND broken = 0 LIMIT 1",
        (item,),
    ).fetchone()
    if row is None:
        return False
    return reach.can_read(item, path=row[0], preset=row[1])


def result_in_reach(
    ctx: Context,
    item: str,
    *,
    result_path: str | None = None,
) -> bool:
    """Result visibility follows item reach plus any explicit ``records/`` path restrictions."""
    if not item_in_reach(ctx, item):
        return False
    reach = getattr(ctx, "reach", None)
    if reach is None or reach.unrestricted or reach.read_all:
        return True
    if result_path is None:
        return True
    record_prefixes = tuple(
        prefix for prefix in reach.read_prefixes if prefix.startswith("records/")
    )
    if not record_prefixes:
        return True
    from context_rail.kernel.reach import under_prefix

    return any(under_prefix(prefix, result_path) for prefix in record_prefixes)


def compiled_results_for_item(
    ctx: Context, item: str
) -> CompiledResults | None:
    """Return the compiled produce/review contracts for ``item``'s workflow, or None."""
    row = ctx.index.conn.execute(
        "SELECT workflow FROM docs WHERE slug = ? AND primitive = 'item' AND broken = 0 LIMIT 1",
        (item,),
    ).fetchone()
    if row is None:
        return None
    wf_name = row[0] or "default"
    model = getattr(ctx, "model", None)
    results_map = getattr(model, "results", {}) if model is not None else {}
    return (results_map or {}).get(wf_name)


def compiled_artifact_dict(artifact: CompiledArtifact) -> dict[str, Any]:
    """Serialize one compiled artifact without leaking author-only terminology."""
    return {
        "preset": artifact.preset,
        "sections": list(artifact.sections),
        "required": list(artifact.required),
        "defaults": dict(artifact.defaults),
        "fields": {key: list(values) for key, values in artifact.fields.items()},
        "template": artifact.template,
        "placement": artifact.placement,
        "digest": artifact.digest,
    }


def result_context(
    ctx: Context,
    item: str,
    *,
    include_body: bool,
    body_chars: int = RESULT_BODY_BRIEF_CHARS,
    history_limit: int = RESULT_HISTORY_LIMIT,
    candidate_commit: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Selected producer/reviewer result facts, bounded body and SQL history for ``item``."""
    compiled = compiled_results_for_item(ctx, item)
    if not item_in_reach(ctx, item):
        return {
            "item": item,
            "contract_enabled": compiled is not None,
            "selected": None,
            "binding": None,
            "current": False,
            "completed": False,
            "reasons": ("outside read reach",),
            "review": None,
            "body": None,
            "body_total_chars": 0,
            "body_omitted_chars": 0,
            "body_clipped": False,
            "history": [],
            "history_omitted": 0,
            "omitted_outside_reach": 1,
        }

    contract_enabled = compiled is not None
    all_rows = query.results_for_item(ctx.index, item, limit=100)
    total_count = query.result_count(ctx.index, item)
    reachable_rows = [
        row
        for row in all_rows
        if result_in_reach(ctx, item, result_path=row.get("path"))
    ]
    visible_history = [
        _summary_from_row(row) for row in reachable_rows[:history_limit]
    ]
    if total_count > len(all_rows) and len(reachable_rows) == len(all_rows):
        history_omitted = max(total_count - len(visible_history), 0)
    else:
        history_omitted = max(len(reachable_rows) - len(visible_history), 0)

    if contract_enabled:
        selection = select_result(
            ctx,
            item,
            record_id=record_id,
            candidate_commit=candidate_commit,
        )
        sel_row = selection.record
        binding = selection.binding
        current = selection.current
        completed = selection.completed
        reasons = selection.reasons
    else:
        sel_row = reachable_rows[0] if reachable_rows else None
        binding = None
        current = sel_row is not None and bool(sel_row.get("valid", False))
        completed = current and sel_row.get("outcome") == "completed"
        reasons = ()

    if sel_row is not None and not result_in_reach(
        ctx, item, result_path=sel_row.get("path")
    ):
        return {
            "item": item,
            "contract_enabled": contract_enabled,
            "selected": None,
            "binding": None,
            "current": False,
            "completed": False,
            "reasons": ("outside read reach",),
            "review": None,
            "body": None,
            "body_total_chars": 0,
            "body_omitted_chars": 0,
            "body_clipped": False,
            "history": [],
            "history_omitted": 0,
            "omitted_outside_reach": 1,
        }

    review_row: dict[str, Any] | None = None
    if sel_row is not None and sel_row.get("record_id"):
        review_row = query.review_for_result(ctx.index, item, sel_row["record_id"])
    if review_row is None:
        review_row = query.review_for_item(ctx.index, item)

    selected_summary = None if sel_row is None else _summary_from_row(sel_row)
    body_text: str | None = None
    body_total_chars = 0
    body_omitted_chars = 0
    body_clipped = False
    if include_body and sel_row is not None and sel_row.get("path"):
        raw_body = _read_selected_body(Path(ctx.store), sel_row["path"])
        if raw_body is not None:
            body_total_chars = len(raw_body)
            cap = max(0, body_chars)
            if len(raw_body) > cap:
                body_text = raw_body[:cap].rstrip()
                body_omitted_chars = max(body_total_chars - len(body_text), 0)
                body_clipped = True
            else:
                body_text = raw_body

    return {
        "item": item,
        "contract_enabled": contract_enabled,
        "selected": selected_summary,
        "binding": (
            None
            if binding is None
            else {
                "record": binding.record,
                "body_hash": binding.body_hash,
                "item_digest": binding.item_digest,
                "work_cycle": binding.work_cycle,
                "contract_digest": binding.contract_digest,
                "candidate_commit": binding.candidate_commit,
            }
        ),
        "current": current,
        "completed": completed,
        "reasons": reasons,
        "review": (
            None
            if review_row is None
            else {
                "record_id": review_row.get("record_id"),
                "path": review_row.get("path"),
                "reviewer": review_row.get("reviewer"),
                "verdict": review_row.get("verdict"),
                "result_record": review_row.get("result_record"),
                "candidate_commit": review_row.get("candidate_commit"),
            }
        ),
        "body": body_text,
        "body_total_chars": body_total_chars,
        "body_omitted_chars": body_omitted_chars,
        "body_clipped": body_clipped,
        "history": visible_history,
        "history_omitted": history_omitted,
        "omitted_outside_reach": 0,
    }


def result_summaries(
    ctx: Context, items: Sequence[str], *, limit: int = RESULT_SUMMARY_LIMIT
) -> list[dict[str, Any]]:
    """Batched, reach-filtered SQL summary read (latest result per item, never reads bodies)."""
    ordered_items = [slug for slug in dict.fromkeys(items) if isinstance(slug, str) and slug]
    if not ordered_items or limit <= 0:
        return []

    visible_items = [slug for slug in ordered_items if item_in_reach(ctx, slug)]
    if not visible_items:
        return []

    latest_by_item: dict[str, dict[str, Any]] = {}
    reviews_by_result: dict[tuple[str, str], dict[str, Any]] = {}
    for start in range(0, len(visible_items), _SQL_BATCH):
        chunk = visible_items[start : start + _SQL_BATCH]
        marks = ",".join("?" for _ in chunk)
        rows = ctx.index.conn.execute(
            "SELECT path, record_id, item, at, order_at, actor, outcome, candidate_commit, "
            "body_hash, item_digest, work_cycle, contract_digest, valid, problem, data_json "
            f"FROM results WHERE item IN ({marks}) "
            "ORDER BY order_at DESC, record_id DESC",
            chunk,
        ).fetchall()
        for row in rows:
            (
                path,
                record_id,
                item_slug,
                at,
                _order_at,
                actor,
                outcome,
                candidate_commit,
                body_hash,
                item_digest,
                work_cycle,
                contract_digest,
                valid,
                problem,
                data_json,
            ) = row
            if item_slug in latest_by_item:
                continue
            if not result_in_reach(ctx, item_slug, result_path=path):
                continue
            latest_by_item[item_slug] = _summary_from_values(
                record_id=record_id,
                path=path,
                item=item_slug,
                at=at,
                actor=actor,
                outcome=outcome,
                candidate_commit=candidate_commit,
                body_hash=body_hash,
                item_digest=item_digest,
                work_cycle=work_cycle,
                contract_digest=contract_digest,
                valid=bool(valid),
                problem=problem,
                data_json=data_json,
            )

        rev_rows = ctx.index.conn.execute(
            "SELECT r.record_id, r.item, r.result_record, r.verdict, r.reviewer, r.path "
            "FROM reviews r JOIN docs d ON d.path = r.path "
            f"WHERE r.item IN ({marks}) AND r.result_record IS NOT NULL "
            "ORDER BY d.record_at DESC, r.record_id DESC",
            chunk,
        ).fetchall()
        for rev_id, rev_item, res_id, verdict, reviewer, rev_path in rev_rows:
            key = (rev_item, res_id)
            if key not in reviews_by_result:
                reviews_by_result[key] = {
                    "record_id": rev_id,
                    "verdict": verdict,
                    "reviewer": reviewer,
                    "path": rev_path,
                }

    summaries: list[dict[str, Any]] = []
    for slug in visible_items:
        entry = latest_by_item.get(slug)
        if entry is None:
            continue
        review = reviews_by_result.get((slug, entry["record_id"]))
        entry["review"] = review
        summaries.append(entry)
        if len(summaries) >= limit:
            break
    return summaries


def render_worker_contract(artifact: CompiledArtifact, item: str) -> str:
    """Render compiled producer contract headings, required/default metadata and command."""
    required = [key for key in artifact.required if key != "workflow_name"]
    lines = [
        f"artifact preset: {artifact.preset}",
        "required sections: " + ", ".join(artifact.sections),
    ]
    if required:
        lines.append("required metadata: " + ", ".join(required))
    if artifact.defaults:
        defaults = ", ".join(f"{k}={v}" for k, v in sorted(artifact.defaults.items()))
        lines.append("default metadata: " + defaults)
    if artifact.fields:
        enums = ", ".join(
            f"{k}=[{'|'.join(str(v) for v in vals)}]"
            for k, vals in sorted(artifact.fields.items())
        )
        lines.append("metadata choices: " + enums)
    lines.append(
        f"record command: rail loop result {item} --body-file <file> "
        "[--commit <sha>] [--outcome completed|partial|blocked|failed] [--set KEY=VALUE]"
    )
    return "\n".join(lines)


def render_result_pointer_line(context: Mapping[str, Any]) -> str:
    """Render the non-trimmable identity and read pointer line for a selected result."""
    selected = context.get("selected")
    if selected is None:
        reasons = context.get("reasons") or ()
        detail = "; ".join(reasons) if reasons else "no producer result recorded"
        return f"result: missing ({detail})"
    rec_id = selected.get("record_id") or "-"
    path = selected.get("path") or "-"
    producer = selected.get("producer") or selected.get("actor") or "-"
    outcome = selected.get("outcome") or "unknown"
    commit = selected.get("candidate_commit")
    commit_note = f", commit {commit[:12]}" if isinstance(commit, str) and commit else ", commitless"
    status = "current" if context.get("current") else "not-current"
    if not context.get("current") and context.get("reasons"):
        status = f"not-current ({'; '.join(context['reasons'])})"
    return (
        f"result: {rec_id} | path: {path} | producer: {producer} | "
        f"outcome: {outcome} | status: {status}{commit_note} | "
        f"read: rail read show {rec_id} --path {path}"
    )


def render_result_details(
    context: Mapping[str, Any], *, max_body_chars: int = RESULT_BODY_BRIEF_CHARS
) -> tuple[str, str]:
    """Return ``(fixed_identity_block, clipped_body_prose)`` kept strictly separate."""
    fixed_lines = [render_result_pointer_line(context)]
    binding = context.get("binding")
    if isinstance(binding, Mapping):
        fixed_lines.append(
            f"binding: body_hash {str(binding.get('body_hash') or '-')[:16]}.. "
            f"| item_digest {str(binding.get('item_digest') or '-')[:16]}.. "
            f"| cycle {binding.get('work_cycle') or 'initial'}"
        )
    review = context.get("review")
    if isinstance(review, Mapping):
        fixed_lines.append(
            f"latest review: {review.get('record_id')} ({review.get('verdict')}) "
            f"by {review.get('reviewer') or '-'}"
        )
    body = context.get("body")
    if not isinstance(body, str) or not body.strip():
        return "\n".join(fixed_lines), ""

    omitted = int(context.get("body_omitted_chars") or 0)
    clipped = bool(context.get("body_clipped"))
    text = body
    if len(text) > max_body_chars:
        cut = max(0, max_body_chars)
        omitted += len(text) - cut
        text = text[:cut].rstrip()
        clipped = True
    if clipped or omitted > 0:
        selected = context.get("selected") or {}
        rec_id = selected.get("record_id") or "-"
        path = selected.get("path") or "-"
        text = (
            f"{text}\n\n[clipped evidence: {omitted} character(s) omitted — not complete proof; "
            f"read full result with `rail read show {rec_id} --path {path}`]"
        )
    return "\n".join(fixed_lines), text


def render_result_summaries(summaries: Sequence[Mapping[str, Any]]) -> list[str]:
    """Format SQL summary rows without reading or embedding bodies."""
    lines: list[str] = []
    for row in summaries:
        rec_id = row.get("record_id") or "-"
        path = row.get("path") or "-"
        item = row.get("item") or "-"
        outcome = row.get("outcome") or "unknown"
        producer = row.get("producer") or row.get("actor") or "-"
        valid = "valid" if row.get("valid") else f"invalid ({row.get('problem') or 'error'})"
        review = row.get("review")
        rev_text = (
            f"; review {review.get('verdict')} ({review.get('record_id')})"
            if isinstance(review, Mapping)
            else ""
        )
        lines.append(
            f"{item}: {rec_id} [{outcome}, {valid}] by {producer} ({path}){rev_text}"
        )
    return lines


def _read_selected_body(store: Path, rel: str) -> str | None:
    try:
        raw = confine(store, rel).read_bytes()
    except (OSError, PathEscape):
        return None
    doc = parse_doc(rel, raw)
    return doc.body.strip()


def _summary_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
    return {
        "record_id": row.get("record_id"),
        "path": row.get("path"),
        "item": row.get("item"),
        "at": row.get("at"),
        "actor": row.get("actor"),
        "producer": data.get("seat") or row.get("actor"),
        "seat": data.get("seat"),
        "outcome": row.get("outcome"),
        "candidate_commit": row.get("candidate_commit"),
        "body_hash": row.get("body_hash"),
        "item_digest": row.get("item_digest"),
        "work_cycle": row.get("work_cycle"),
        "contract_digest": row.get("contract_digest"),
        "valid": bool(row.get("valid", False)),
        "problem": row.get("problem"),
    }


def _summary_from_values(
    *,
    record_id: str,
    path: str,
    item: str,
    at: str | None,
    actor: str | None,
    outcome: str | None,
    candidate_commit: str | None,
    body_hash: str | None,
    item_digest: str | None,
    work_cycle: str | None,
    contract_digest: str | None,
    valid: bool,
    problem: str | None,
    data_json: str,
) -> dict[str, Any]:
    import json

    try:
        data = json.loads(data_json)
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "record_id": record_id,
        "path": path,
        "item": item,
        "at": at,
        "actor": actor,
        "producer": data.get("seat") or actor,
        "seat": data.get("seat"),
        "outcome": outcome,
        "candidate_commit": candidate_commit,
        "body_hash": body_hash,
        "item_digest": item_digest,
        "work_cycle": work_cycle,
        "contract_digest": contract_digest,
        "valid": valid,
        "problem": problem,
    }
