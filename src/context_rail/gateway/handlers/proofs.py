"""Shared gate proof and completion-fact collection for moves and assessments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.gateway.handlers.common import (
    check_gate_state,
    gate_candidate,
    review_gate_state,
)
from context_rail.kernel import gates
from context_rail.model.errors import ModelError
from context_rail.model.results import (
    parse_review,
    result_body_hash,
    validate_result_body,
)
from context_rail.store import query
from context_rail.store.doc import parse_doc, record_digest, record_frontmatter
from context_rail.store.location import PathEscape, confine
from context_rail.store.results import historical_result_exempt, select_result

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["CompletionFacts", "completion_facts", "verify_contract_review"]


@dataclass(frozen=True)
class CompletionFacts:
    """All gate proof facts gathered once for one item and target status."""

    candidate: str | None
    checks: tuple[gates.CheckState, ...]
    review: gates.ReviewState | None
    result: gates.ResultState | None
    result_required: bool
    missing: tuple[str, ...]
    stale: tuple[str, ...]
    historical_exempt: bool = False


def completion_facts(
    ctx: Context,
    item: str,
    target: str,
    *,
    workflow_name: str | None = None,
    assessment: bool = False,
) -> CompletionFacts:
    """Gather checks, candidate, review and producer result proofs for ``item``."""
    wf_name = workflow_name
    if wf_name is None:
        row = ctx.index.conn.execute(
            "SELECT workflow FROM docs WHERE slug = ? AND primitive = 'item' AND broken = 0 LIMIT 1",
            (item,),
        ).fetchone()
        wf_name = (row[0] or "default") if row is not None else "default"

    workflow = ctx.pack.workflows.get(wf_name)
    required = workflow.checks_for(target) if workflow is not None else None
    rows = query.check_states(ctx.index, item)
    missing: list[str] = []
    if required is None:
        selected = rows
    else:
        by_slug = {row["slug"]: row for row in rows}
        selected = [by_slug[slug] for slug in required if slug in by_slug]
        missing = [slug for slug in required if slug not in by_slug]
    states = tuple(check_gate_state(ctx, row) for row in selected)
    stale = tuple(
        state.slug
        for state in states
        if state.freshness != "fresh" or not state.passed
    )

    compiled_results = getattr(ctx, "model", None)
    results_map = getattr(compiled_results, "results", {}) if compiled_results is not None else {}
    compiled_protocol = (results_map or {}).get(wf_name)
    contract_enabled = compiled_protocol is not None

    exempt = bool(assessment and contract_enabled and historical_result_exempt(ctx, item))
    result_required = contract_enabled and not exempt

    latest_cand = query.latest_candidate(ctx.index, item)
    latest_cand_commit = str(latest_cand["commit"]) if latest_cand is not None else None

    result_state: gates.ResultState | None = None
    if result_required:
        selection = select_result(ctx, item, candidate_commit=latest_cand_commit)
        result_state = gates.ResultState(
            record=selection.record["record_id"] if selection.record is not None else None,
            body_hash=selection.binding.body_hash if selection.binding is not None else None,
            current=selection.current,
            completed=selection.completed,
            reasons=selection.reasons,
        )

    review_row = query.review_for_item(ctx.index, item)
    review_state: gates.ReviewState | None = None
    if review_row is not None:
        is_legacy_json = (
            exempt
            and isinstance(review_row.get("path"), str)
            and review_row["path"].lower().endswith(".json")
            and not (review_row.get("data") or {}).get("review_contract_digest")
        )
        if contract_enabled and not is_legacy_json:
            rev_current, rev_reasons = verify_contract_review(
                ctx,
                review_row,
                item=item,
                review_artifact=compiled_protocol.review,
                latest_candidate=latest_cand_commit,
                result_state=result_state,
            )
            review_state = review_gate_state(
                review_row, current=rev_current, reasons=rev_reasons
            )
        else:
            review_state = review_gate_state(review_row)

    if contract_enabled and not exempt:
        candidate = latest_cand_commit
    else:
        candidate = gate_candidate(ctx.index, item, review_state, list(states))

    return CompletionFacts(
        candidate=candidate,
        checks=states,
        review=review_state,
        result=result_state,
        result_required=result_required,
        missing=tuple(missing),
        stale=stale,
        historical_exempt=exempt,
    )


def verify_contract_review(
    ctx: Context,
    review_row: dict[str, Any],
    *,
    item: str,
    review_artifact: Any,
    latest_candidate: str | None,
    result_state: gates.ResultState | None,
) -> tuple[bool, tuple[str, ...]]:
    """Verify Markdown review integrity, contract digest, and bindings without git."""
    reasons: list[str] = []
    rel = review_row.get("path")
    if not isinstance(rel, str) or not rel:
        return False, ("review record path is missing",)

    if not rel.lower().endswith(".md"):
        reasons.append("review record is not a Markdown contract review")
        return False, tuple(reasons)

    try:
        raw = confine(Path(ctx.store), rel).read_bytes()
    except (OSError, PathEscape) as exc:
        return False, (f"review record file {rel!r} cannot be read: {exc}",)

    doc = parse_doc(rel, raw)
    if doc.problems:
        return False, ("review record has document problems: " + ", ".join(doc.problems),)

    front = record_frontmatter(raw)
    if front is None:
        return False, ("review record has no readable frontmatter",)

    stored_digest = front.get("digest")
    if not isinstance(stored_digest, str) or stored_digest != record_digest(front):
        reasons.append("review record frontmatter digest does not match immutable contents")

    raw_data = front.get("data")
    if not isinstance(raw_data, dict):
        reasons.append("review record data must be a mapping")
        return False, tuple(reasons)

    try:
        payload = parse_review(raw_data)
    except ModelError as exc:
        reasons.append(f"review payload is invalid: {exc.message}")
        return False, tuple(reasons)

    if payload.get("item") != item or doc.relations.get("about", ()) != (item,):
        reasons.append("review item identity does not match envelope about")
    if front.get("actor") != payload.get("reviewer_actor"):
        reasons.append("review actor does not match envelope actor")

    if payload.get("review_contract_digest") != review_artifact.digest:
        reasons.append("compiled review artifact contract changed since this review was recorded")

    try:
        validate_result_body(doc.body, review_artifact.sections)
    except ModelError as exc:
        reasons.append(exc.message)

    actual_body_hash = result_body_hash(doc.body)
    if payload.get("body_hash") != actual_body_hash:
        reasons.append("review body_hash does not match Markdown body")

    bound_commit = payload.get("candidate_commit")
    if latest_candidate is not None and bound_commit != latest_candidate:
        reasons.append(
            f"review is bound to candidate {bound_commit!r}, not current candidate {latest_candidate!r}"
        )
    elif latest_candidate is None and bound_commit is not None:
        reasons.append(
            f"review names candidate commit {bound_commit!r}, but no candidate is current"
        )

    if result_state is not None:
        if not result_state.current:
            reasons.append("bound producer result is no longer current")
        elif result_state.record is not None and payload.get("result_record") != result_state.record:
            reasons.append(
                f"review is bound to result {payload.get('result_record')!r}, not current result {result_state.record!r}"
            )
        elif (
            result_state.body_hash is not None
            and payload.get("result_body_hash") != result_state.body_hash
        ):
            reasons.append("review result_body_hash does not match current producer result")

    return not reasons, tuple(reasons)
