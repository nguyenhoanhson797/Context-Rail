"""Select, bind and verify immutable Markdown result evidence for an item."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from context_rail.core.util import canonical_iso
from context_rail.kernel.lifecycle import category_of
from context_rail.model.errors import ModelError
from context_rail.model.primitives import FINISHED_CATEGORIES
from context_rail.model.results import (
    RESULT_KIND,
    ResultBinding,
    ResultSelection,
    parse_result,
    result_body_hash,
    validate_result_body,
)
from context_rail.store import query
from context_rail.store.approval import semantic_hash
from context_rail.store.doc import parse_doc, record_digest, record_frontmatter
from context_rail.store.location import PathEscape, confine

__all__ = ["item_content_digest", "select_result", "work_cycle"]


def item_content_digest(store: Path, item_path: str) -> str:
    """Semantic hash of an item document, ignoring lifecycle-only fields."""
    target = confine(Path(store), item_path)
    return semantic_hash(target.read_bytes(), format="markdown")


def work_cycle(context: Any, item: str) -> str | None:
    """ULID of the latest finished-to-unfinished lifecycle event for ``item``, or ``None``.

    Both ``status.changed`` moves and ``pack.changed`` item status/workflow
    migrations can reopen a finished item; initial work before any reopen
    returns ``None``.
    """
    rows = context.index.conn.execute(
        """
        SELECT d.path, d.slug, f1.value_json, f2.value_json
        FROM docs d
        JOIN relations r ON r.src_path = d.path AND r.rel = 'about' AND r.dst = ?
        JOIN fields f1 ON f1.path = d.path AND f1.key = 'kind'
        JOIN fields f2 ON f2.path = d.path AND f2.key = 'data'
        WHERE d.primitive = 'record' AND d.broken = 0
          AND f1.value_json IN ('"status.changed"', '"pack.changed"')
        ORDER BY d.record_at DESC, d.slug DESC
        """,
        (item,),
    ).fetchall()
    for _path, slug, kind_json, data_json in rows:
        try:
            kind = json.loads(kind_json)
            data = json.loads(data_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if kind == "status.changed":
            if _reopened_from_status_changed(context.pack, data):
                return slug
        elif kind == "pack.changed":
            if _reopened_from_pack_changed(context.pack, item, data):
                return slug
    return None


def _reopened_from_status_changed(pack: Any, data: Mapping[str, Any]) -> bool:
    from_cat = data.get("from_category")
    to_cat = data.get("to_category")
    if not isinstance(from_cat, str):
        from_status = data.get("from")
        from_cat = category_of(pack, from_status) if isinstance(from_status, str) else None
    if not isinstance(to_cat, str):
        to_status = data.get("to")
        to_cat = category_of(pack, to_status) if isinstance(to_status, str) else None
    return (
        from_cat in FINISHED_CATEGORIES
        and to_cat is not None
        and to_cat not in FINISHED_CATEGORIES
    )


def _reopened_from_pack_changed(pack: Any, item: str, data: Mapping[str, Any]) -> bool:
    for change in data.get("changes") or ():
        if not isinstance(change, Mapping) or change.get("slug") != item:
            continue
        from_cat = change.get("from_category")
        to_cat = change.get("to_category")
        if not isinstance(from_cat, str):
            from_status = change.get("from")
            from_cat = (
                category_of(pack, from_status) if isinstance(from_status, str) else None
            )
        if not isinstance(to_cat, str):
            to_status = change.get("to")
            to_cat = category_of(pack, to_status) if isinstance(to_status, str) else None
        if (
            from_cat in FINISHED_CATEGORIES
            and to_cat is not None
            and to_cat not in FINISHED_CATEGORIES
        ):
            return True
    return False


def select_result(
    context: Any,
    item: str,
    *,
    record_id: str | None = None,
    candidate_commit: str | None = None,
) -> ResultSelection:
    """Select the newest (or explicit) producer result and evaluate its binding and integrity.

    Selection always inspects the newest producer attempt first; a newer
    partial/blocked/failed/malformed/tampered record never falls back to an
    older completed one. Explicit ``record_id`` queries remain diagnostic when
    the requested record is not the newest or belongs to a superseded candidate.
    """
    ix = context.index
    item_row = _item_doc_row(ix, item)
    if item_row is None:
        return ResultSelection(
            record=None,
            binding=None,
            current=False,
            completed=False,
            reasons=(f"item {item!r} was not found in the index",),
        )
    item_path, item_workflow = item_row

    compiled_results = getattr(context.model, "results", {}) or {}
    compiled_protocol = compiled_results.get(item_workflow)
    if compiled_protocol is None:
        return ResultSelection(
            record=None,
            binding=None,
            current=False,
            completed=False,
            reasons=(f"workflow {item_workflow!r} declares no result contract",),
        )
    artifact = compiled_protocol.produce

    latest_rows = query.results_for_item(ix, item, limit=1)
    latest_for_item = latest_rows[0] if latest_rows else None

    if record_id is not None:
        selected = query.result_by_record(ix, record_id)
        if selected is None:
            return ResultSelection(
                record=None,
                binding=None,
                current=False,
                completed=False,
                reasons=(f"result record {record_id!r} was not found",),
            )
        if selected.get("item") != item:
            return ResultSelection(
                record=selected,
                binding=_binding_from_row(selected),
                current=False,
                completed=False,
                reasons=(
                    f"result record {record_id!r} belongs to item {selected.get('item')!r}, not {item!r}",
                ),
            )
    else:
        selected = latest_for_item
        if selected is None:
            return ResultSelection(
                record=None,
                binding=None,
                current=False,
                completed=False,
                reasons=(f"no producer result has been recorded for {item!r}",),
            )

    reasons: list[str] = []
    if not selected.get("valid", False):
        problem = selected.get("problem") or "result record payload is invalid"
        reasons.append(str(problem))

    # Re-verify the selected store file directly on disk (bounded single-file read).
    payload, file_problems = _verify_selected_file(
        Path(context.store),
        selected,
        item=item,
        sections=artifact.sections,
    )
    for problem in file_problems:
        if problem not in reasons:
            reasons.append(problem)

    binding = _binding_from_payload(selected, payload)

    if record_id is not None and latest_for_item is not None:
        if latest_for_item["record_id"] != selected["record_id"]:
            reasons.append(
                f"result record {selected['record_id']!r} is superseded by newer result {latest_for_item['record_id']!r}"
            )

    # Compare item content digest, cycle and compiled contract digest.
    try:
        current_item_digest = item_content_digest(Path(context.store), item_path)
    except (OSError, PathEscape, ModelError) as exc:
        current_item_digest = None
        reasons.append(f"cannot compute item content digest: {exc}")

    current_cycle = work_cycle(context, item)

    if payload is not None:
        if current_item_digest is not None and payload.item_digest != current_item_digest:
            reasons.append("item content changed since this result was recorded (item_digest mismatch)")
        if payload.work_cycle != current_cycle:
            reasons.append(
                f"work cycle changed since this result was recorded (bound {payload.work_cycle!r}, current {current_cycle!r})"
            )
        if payload.contract_digest != artifact.digest:
            reasons.append("compiled result artifact contract changed since this result was recorded")

    # Candidate binding: compare against caller's requested candidate AND latest item candidate.
    latest_cand = query.latest_candidate(ix, item)
    latest_commit = latest_cand["commit"] if latest_cand is not None else None
    bound_commit = payload.candidate_commit if payload is not None else selected.get("candidate_commit")
    if candidate_commit is not None and bound_commit != candidate_commit:
        reasons.append(
            f"result is bound to candidate commit {bound_commit!r}, not requested candidate {candidate_commit!r}"
        )
    if latest_commit is not None and bound_commit != latest_commit:
        reasons.append(
            f"result is bound to candidate commit {bound_commit!r}, not current candidate {latest_commit!r}"
        )
    if latest_commit is None and candidate_commit is None and bound_commit is not None:
        reasons.append(
            f"result names candidate commit {bound_commit!r}, which does not exist for {item!r}"
        )

    current = not reasons
    outcome = payload.outcome if payload is not None else selected.get("outcome")
    completed = current and outcome == "completed"
    if current and not completed:
        reasons.append(f"newest result outcome is {outcome!r}, not 'completed'")

    return ResultSelection(
        record=selected,
        binding=binding,
        current=current,
        completed=completed,
        reasons=tuple(reasons),
    )


def _item_doc_row(ix: Any, slug: str) -> tuple[str, str] | None:
    rows = ix.conn.execute(
        "SELECT path, workflow FROM docs WHERE slug = ? AND primitive = 'item' AND broken = 0 ORDER BY path",
        (slug,),
    ).fetchall()
    if len(rows) != 1:
        return None
    path, workflow = rows[0]
    return path, workflow or "default"


def _binding_from_row(row: Mapping[str, Any]) -> ResultBinding | None:
    record_id = row.get("record_id")
    body_hash = row.get("body_hash")
    item_digest = row.get("item_digest")
    contract_digest = row.get("contract_digest")
    if not all(isinstance(v, str) and v for v in (record_id, body_hash, item_digest, contract_digest)):
        return None
    return ResultBinding(
        record=str(record_id),
        body_hash=str(body_hash),
        item_digest=str(item_digest),
        work_cycle=row.get("work_cycle"),
        contract_digest=str(contract_digest),
        candidate_commit=row.get("candidate_commit"),
    )


def _binding_from_payload(
    row: Mapping[str, Any], payload: Any
) -> ResultBinding | None:
    if payload is not None:
        return ResultBinding(
            record=str(row["record_id"]),
            body_hash=payload.body_hash,
            item_digest=payload.item_digest,
            work_cycle=payload.work_cycle,
            contract_digest=payload.contract_digest,
            candidate_commit=payload.candidate_commit,
        )
    return _binding_from_row(row)


def _verify_selected_file(
    store: Path,
    row: Mapping[str, Any],
    *,
    item: str,
    sections: tuple[str, ...],
) -> tuple[Any | None, list[str]]:
    problems: list[str] = []
    rel = row.get("path")
    if not isinstance(rel, str) or not rel:
        return None, ["result record path is missing"]
    try:
        path = confine(store, rel)
        raw = path.read_bytes()
    except (OSError, PathEscape) as exc:
        return None, [f"result record file {rel!r} cannot be read: {exc}"]

    doc = parse_doc(rel, raw)
    if doc.problems:
        problems.append("result record has document problems: " + ", ".join(doc.problems))
        return None, problems

    front = record_frontmatter(raw)
    if front is None:
        problems.append("result record has no readable frontmatter")
        return None, problems

    stored_digest = front.get("digest")
    if not isinstance(stored_digest, str) or stored_digest != record_digest(front):
        problems.append("result record frontmatter digest does not match immutable contents")

    if front.get("kind") != RESULT_KIND:
        problems.append(f"result record kind {front.get('kind')!r} is not {RESULT_KIND!r}")

    envelope_actor = front.get("actor")
    envelope_at = front.get("at")
    about = doc.relations.get("about", ())
    if not isinstance(envelope_at, str) or canonical_iso(envelope_at) is None:
        problems.append("result record envelope is missing or has an invalid 'at'")

    raw_data = front.get("data")
    if not isinstance(raw_data, dict):
        problems.append("result record data must be a mapping")
        return None, problems

    payload = None
    try:
        payload = parse_result(raw_data)
    except ModelError as exc:
        problems.append(f"result payload is invalid: {exc.message}")
        return None, problems

    if payload.item != item or about != (item,):
        problems.append("result item identity does not match envelope about")
    if not isinstance(envelope_actor, str) or payload.actor != envelope_actor:
        problems.append("result actor does not match envelope actor")

    try:
        validate_result_body(doc.body, sections)
    except ModelError as exc:
        problems.append(exc.message)

    actual_body_hash = result_body_hash(doc.body)
    if actual_body_hash != payload.body_hash:
        problems.append("result body_hash does not match Markdown body")

    return payload, problems
