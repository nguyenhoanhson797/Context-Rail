"""Review verdicts: contract-enabled Markdown reviews and legacy candidate reviews."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.gateway.handlers import checks as checks_handler
from context_rail.gateway.handlers.common import checkout, preset_for
from context_rail.gateway.handlers.independence import independence_refusal
from context_rail.gateway.handlers.results import artifact_fields, read_result_body
from context_rail.kernel.rights import separation_problem
from context_rail.model.candidate import TEXT_CAP
from context_rail.model.errors import ModelError
from context_rail.model.results import (
    REVIEW_SCHEMA,
    parse_review,
    result_body_hash,
    validate_result_body,
)
from context_rail.runner import git
from context_rail.store import query
from context_rail.store.location import local_dir
from context_rail.store.lock import store_lock
from context_rail.store.results import select_result
from context_rail.store.write import ABSENT, WriteRefused, replay
from context_rail.store.write import commit as commit_records

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["record_review", "verify_review_checks"]

NOTES_MAX = 4000
_VERDICTS = ("approve", "reject")


def record_review(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Record one reviewer verdict, then verify review-mode checks."""
    key = args.get("key")
    with store_lock(ctx.store):
        ctx.index.sync()
        saved = replay(ctx.store, key)
        if saved is not None:
            found = query.review_by_record(ctx.index, saved.get("record") or "")
            if found is None:
                return {"record": saved.get("record"), "replayed": True}
            return {**_review_value(found), "checks_verified": [], "replayed": True}
        value = _record_review(ctx, args, actor, key)
    value["checks_verified"] = verify_review_checks(ctx, value["item"], actor)
    return value


def _record_review(
    ctx: Context, args: dict[str, Any], actor: str, key: str | None
) -> dict[str, Any]:
    slug = _required(args.get("item"), "item")
    verdict = _required(args.get("verdict"), "verdict")
    if verdict not in _VERDICTS:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a verdict is approve or reject, not {verdict!r}",
            {"param": "verdict"},
        )
    reviewer_model = _free_text(args.get("model"), "model")
    harness = _free_text(args.get("harness"), "harness")
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )

    item_row = _resolve_item_row(ctx, slug)
    workflow_name = item_row[1]
    compiled_protocol = (getattr(ctx.model, "results", {}) or {}).get(workflow_name)
    if compiled_protocol is None:
        return _record_legacy_review(
            ctx,
            slug=slug,
            verdict=verdict,
            reviewer_model=reviewer_model,
            harness=harness,
            args=args,
            actor=actor,
            key=key,
            policy=policy,
        )

    return _record_contract_review(
        ctx,
        slug=slug,
        workflow_name=workflow_name,
        review_artifact=compiled_protocol.review,
        verdict=verdict,
        reviewer_model=reviewer_model,
        harness=harness,
        args=args,
        actor=actor,
        key=key,
        policy=policy,
    )


def _resolve_item_row(ctx: Context, slug: str) -> tuple[str, str]:
    rows = ctx.index.conn.execute(
        "SELECT path, primitive, workflow, broken FROM docs WHERE slug = ? ORDER BY path",
        (slug,),
    ).fetchall()
    if not rows:
        raise WriteRefused("NOT_FOUND", f"{slug!r} is not an item", {"item": slug})
    if len(rows) > 1:
        raise WriteRefused(
            "CONFLICT",
            f"the slug {slug!r} matches {len(rows)} documents",
            {"item": slug, "paths": [r[0] for r in rows]},
        )
    path, primitive, workflow, broken = rows[0]
    if primitive != "item" or broken:
        raise WriteRefused("NOT_FOUND", f"{slug!r} is not an item", {"item": slug})
    return path, workflow or "default"


def _record_legacy_review(
    ctx: Context,
    *,
    slug: str,
    verdict: str,
    reviewer_model: str | None,
    harness: str | None,
    args: dict[str, Any],
    actor: str,
    key: str | None,
    policy: Any,
) -> dict[str, Any]:
    if args.get("result") is not None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"workflow for {slug!r} has no result contract; --result is not supported",
            {"param": "result"},
        )
    if args.get("set"):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"workflow for {slug!r} has no result contract; --set is not supported",
            {"param": "set"},
        )
    rev = _required(args.get("commit"), "commit")
    notes = _notes(args.get("notes_file"))
    root, _git_dir = checkout(ctx, action="recording a review")
    commit = _resolve(root, rev, "commit")
    candidate = query.candidate_for_commit(ctx.index, slug, commit)
    if candidate is None:
        raise WriteRefused(
            "INVALID_STATE",
            f"no candidate was recorded for {slug!r} at {commit[:12]}; "
            f"record it first with `rail loop candidate {slug} --commit {commit[:12]}`",
            {"reason": "no_candidate", "item": slug, "commit": commit},
        )
    implementer = str(candidate["seat"] or candidate["actor"])
    implementer_model = candidate["data"].get("model")
    problem = separation_problem(
        implementer,
        actor,
        implementer_model,
        reviewer_model,
        policy.reviewer_different_model,
    )
    if problem is not None:
        reason, message = problem
        raise WriteRefused(
            "PERMISSION_DENIED",
            message,
            {"reason": reason, "item": slug, "commit": commit},
        )
    identity = ctx.identity
    seat = identity.seat if identity.kind == "seat" else None
    role = identity.role if seat is not None else None
    if seat is not None and role is not None and role.independent_of:
        refusal = independence_refusal(
            ctx,
            item=slug,
            step_kinds=role.independent_of,
            acting=(seat.seat, seat.profile or ""),
        )
        if refusal is not None:
            raise WriteRefused(
                "PERMISSION_DENIED",
                refusal,
                {
                    "reason": "independence_violation",
                    "item": slug,
                    "steps": list(role.independent_of),
                },
            )

    payload: dict[str, Any] = {
        "schema": 1,
        "item": slug,
        "implementation_run": None,
        "implementer_actor": implementer,
        "reviewer_actor": actor,
        "candidate_commit": commit,
        "candidate_record": candidate["record_id"],
        "verdict": verdict,
        "findings": [],
        "evidence": [],
    }
    if implementer_model:
        payload["implementer_model"] = implementer_model
    if reviewer_model is not None:
        payload["reviewer_model"] = reviewer_model
    if harness is not None:
        payload["harness"] = harness
    if notes is not None:
        payload["notes"] = notes

    result = commit_records(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "review.verdict", "about": [slug], "data": payload},
        actor=actor,
        key=key,
        index=ctx.index,
    )
    stored = {
        "record_id": result["record"],
        "item": slug,
        "commit": commit,
        "verdict": verdict,
    }
    return {**_review_value(stored), "replayed": False}


def _record_contract_review(
    ctx: Context,
    *,
    slug: str,
    workflow_name: str,
    review_artifact: Any,
    verdict: str,
    reviewer_model: str | None,
    harness: str | None,
    args: dict[str, Any],
    actor: str,
    key: str | None,
    policy: Any,
) -> dict[str, Any]:
    notes_arg = _required(args.get("notes_file"), "notes_file")
    notes_path = _resolve_external_notes_path(ctx.store, notes_arg)
    preset_obj = preset_for(ctx.pack, review_artifact.preset)
    fields = artifact_fields(
        review_artifact, workflow_name, args.get("set") or (), preset_obj=preset_obj
    )

    body = _read_review_body(notes_path)
    try:
        validate_result_body(body, review_artifact.sections)
    except ModelError as exc:
        raise WriteRefused(exc.code, exc.message, exc.details) from None
    body_hash = result_body_hash(body)

    commit_arg = _optional_text(args.get("commit"), "commit")
    result_arg = _optional_text(args.get("result"), "result")

    # Resolve candidate binding without touching git when commitless.
    candidate = _resolve_review_candidate(ctx, slug, commit_arg)
    candidate_commit = candidate["commit"] if candidate is not None else None
    latest_cand = query.latest_candidate(ctx.index, slug)

    if (
        verdict == "approve"
        and candidate is not None
        and latest_cand is not None
        and candidate["commit"] != latest_cand["commit"]
    ):
        raise WriteRefused(
            "INVALID_STATE",
            f"candidate {candidate['commit'][:12]} is superseded by current candidate {latest_cand['commit'][:12]}",
            {
                "reason": "stale_candidate",
                "item": slug,
                "commit": candidate["commit"],
                "current_commit": latest_cand["commit"],
            },
        )

    selection = select_result(
        ctx,
        slug,
        record_id=result_arg,
        candidate_commit=candidate_commit,
    )

    if verdict == "approve":
        if selection.record is None:
            reason = selection.reasons[0] if selection.reasons else "no producer result recorded"
            raise WriteRefused(
                "INVALID_STATE",
                reason,
                {"reason": "no_result", "item": slug},
            )
        if not selection.current or not selection.completed or selection.binding is None:
            reason = "; ".join(selection.reasons) or "producer result is not current completed evidence"
            raise WriteRefused(
                "INVALID_STATE",
                reason,
                {
                    "reason": "result_not_current",
                    "item": slug,
                    "result": selection.record.get("record_id"),
                },
            )
    else:
        # Rejections allow missing evidence (both candidate and result null) or explicit valid item bindings.
        if result_arg is not None:
            if selection.record is None:
                raise WriteRefused(
                    "INVALID_STATE",
                    selection.reasons[0] if selection.reasons else f"result {result_arg!r} not found",
                    {"reason": "no_result", "item": slug, "result": result_arg},
                )
            if selection.record.get("item") != slug:
                raise WriteRefused(
                    "INVALID_STATE",
                    f"result {result_arg!r} belongs to item {selection.record.get('item')!r}, not {slug!r}",
                    {"reason": "result_wrong_item", "item": slug, "result": result_arg},
                )
        if candidate is not None and selection.record is not None and result_arg is not None:
            bound_commit = selection.record.get("candidate_commit")
            if bound_commit != candidate["commit"]:
                raise WriteRefused(
                    "INVALID_STATE",
                    f"result {result_arg!r} is bound to candidate {bound_commit!r}, not {candidate['commit']!r}",
                    {"reason": "candidate_mismatch", "item": slug},
                )

    # Determine producer identity/model from candidate and/or result record.
    prod_row = selection.record if selection.record is not None else None
    candidate_hash = (
        (
            selection.binding.body_hash
            if selection.binding is not None
            else prod_row.get("body_hash")
        )
        if prod_row is not None
        else None
    )
    if (
        verdict == "reject"
        and prod_row is not None
        and (
            prod_row.get("item") != slug
            or (
                candidate is not None
                and prod_row.get("candidate_commit") != candidate["commit"]
            )
            or not candidate_hash
        )
    ):
        if result_arg is not None and not candidate_hash:
            raise WriteRefused(
                "INVALID_STATE",
                "; ".join(selection.reasons)
                or f"result {result_arg!r} has no verifiable body hash",
                {"reason": "result_unhashed", "item": slug, "result": result_arg},
            )
        prod_row = None

    prod_data = (prod_row.get("data") or {}) if prod_row is not None else {}
    cand_data = (candidate.get("data") or {}) if candidate is not None else {}

    if candidate is not None and prod_row is not None:
        cand_id = str(candidate.get("seat") or candidate.get("actor") or "")
        prod_id = str(prod_data.get("seat") or prod_row.get("actor") or "")
        if cand_id and prod_id and cand_id != prod_id:
            raise WriteRefused(
                "PERMISSION_DENIED",
                f"producer result belongs to {prod_id!r}, not candidate producer {cand_id!r}",
                {"reason": "producer_mismatch", "item": slug},
            )

    implementer_actor: str | None = None
    implementer_seat: str | None = None
    implementer_profile: str | None = None
    implementer_model: str | None = None
    if prod_row is not None:
        implementer_actor = (
            prod_data.get("seat")
            or prod_row.get("actor")
            or prod_data.get("actor")
        )
        implementer_seat = prod_data.get("seat")
        implementer_profile = prod_data.get("profile")
        implementer_model = prod_data.get("model")
    if candidate is not None:
        if implementer_actor is None:
            implementer_actor = candidate.get("seat") or candidate.get("actor")
        if implementer_seat is None:
            implementer_seat = candidate.get("seat")
        if implementer_profile is None:
            implementer_profile = cand_data.get("profile")
        if implementer_model is None:
            implementer_model = cand_data.get("model")

    identity = ctx.identity
    seat = identity.seat if identity.kind == "seat" else None
    role = identity.role if seat is not None else None
    reviewer_seat = seat.seat if seat is not None else None
    reviewer_profile = (
        seat.profile
        if seat is not None
        else (actor if ctx.profile is not None and ctx.actor_named else None)
    )

    if implementer_actor is not None:
        problem = separation_problem(
            str(implementer_actor),
            actor,
            implementer_model,
            reviewer_model,
            policy.reviewer_different_model,
        )
        if problem is not None:
            reason, message = problem
            raise WriteRefused(
                "PERMISSION_DENIED",
                message,
                {"reason": reason, "item": slug, "commit": candidate_commit},
            )
    if (
        implementer_profile is not None
        and reviewer_profile is not None
        and implementer_profile == reviewer_profile
    ):
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"the reviewer must use a different profile from {implementer_profile!r}",
            {"reason": "independence_violation", "item": slug},
        )
    if seat is not None and role is not None and role.independent_of:
        refusal_msg = independence_refusal(
            ctx,
            item=slug,
            step_kinds=role.independent_of,
            acting=(seat.seat, seat.profile or ""),
        )
        if refusal_msg is not None:
            raise WriteRefused(
                "PERMISSION_DENIED",
                refusal_msg,
                {
                    "reason": "independence_violation",
                    "item": slug,
                    "steps": list(role.independent_of),
                },
            )

    bound_result_id = prod_row["record_id"] if prod_row is not None else None
    bound_result_hash = (
        (selection.binding.body_hash if selection.binding is not None else prod_row.get("body_hash"))
        if prod_row is not None
        else None
    )

    payload: dict[str, Any] = {
        "schema": REVIEW_SCHEMA,
        "item": slug,
        "implementation_run": None,
        "reviewer_actor": actor,
        "candidate_commit": candidate_commit,
        "result_record": bound_result_id,
        "result_body_hash": bound_result_hash,
        "body_hash": body_hash,
        "review_contract_digest": review_artifact.digest,
        "verdict": verdict,
        "findings": [],
        "evidence": [],
    }
    if implementer_actor is not None:
        payload["implementer_actor"] = str(implementer_actor)
    if implementer_seat is not None:
        payload["implementer_seat"] = str(implementer_seat)
    if implementer_profile is not None:
        payload["implementer_profile"] = str(implementer_profile)
    if reviewer_seat is not None:
        payload["reviewer_seat"] = reviewer_seat
    if reviewer_profile is not None:
        payload["reviewer_profile"] = reviewer_profile
    if candidate is not None:
        payload["candidate_record"] = candidate["record_id"]
    if implementer_model:
        payload["implementer_model"] = implementer_model
    if reviewer_model is not None:
        payload["reviewer_model"] = reviewer_model
    if harness is not None:
        payload["harness"] = harness

    try:
        parse_review(payload)
    except ModelError as exc:
        raise WriteRefused(exc.code, exc.message, exc.details) from None

    record_req = {
        "preset": review_artifact.preset,
        "kind": "review.verdict",
        "about": [slug],
        "title": f"{slug} review",
        "body": body,
        "fields": fields,
        "data": payload,
    }
    committed = commit_records(
        ctx.store,
        ctx.manifest,
        [],
        record_req,
        actor=actor,
        key=key,
        index=ctx.index,
    )
    stored = query.review_by_record(ctx.index, committed["record"])
    if stored is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "the review was written but is not in the index yet",
            {"record": committed["record"]},
        )
    return {**_review_value(stored), "replayed": False}


def _resolve_review_candidate(
    ctx: Context, item: str, commit_arg: str | None
) -> dict[str, Any] | None:
    if commit_arg is None:
        return query.latest_candidate(ctx.index, item)
    needle = commit_arg.strip()
    if len(needle) == 40:
        found = query.candidate_for_commit(ctx.index, item, needle)
        if found is None:
            raise WriteRefused(
                "INVALID_STATE",
                f"no candidate was recorded for {item!r} matching {needle!r}",
                {"reason": "no_candidate", "item": item, "commit": needle},
            )
        return found
    candidates = query.candidates_for(ctx.index, item, limit=100)
    matches = [
        row
        for row in candidates
        if row["commit"] == needle
        or (len(needle) >= 4 and row["commit"].startswith(needle))
    ]
    if not matches:
        raise WriteRefused(
            "INVALID_STATE",
            f"no candidate was recorded for {item!r} matching {needle!r}",
            {"reason": "no_candidate", "item": item, "commit": needle},
        )
    unique_shas = sorted({row["commit"] for row in matches})
    if len(unique_shas) > 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"commit prefix {needle!r} matches multiple recorded candidates for {item!r}",
            {"param": "commit", "value": needle, "matches": unique_shas},
        )
    found = query.candidate_for_commit(ctx.index, item, unique_shas[0])
    assert found is not None
    return found


def _resolve_external_notes_path(store: Path, raw: str) -> Path:
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot resolve notes file {raw!r}: {exc}",
            {"param": "notes_file", "path": raw},
        ) from exc
    store_root = Path(store).resolve()
    if resolved == store_root or resolved.is_relative_to(store_root):
        raise WriteRefused(
            "FORBIDDEN",
            f"notes file {raw!r} must be outside the store",
            {"param": "notes_file", "path": raw},
        )
    if resolved.is_dir():
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"notes file {raw!r} is a directory",
            {"param": "notes_file", "path": raw},
        )
    return resolved


def _read_review_body(path: Path) -> str:
    try:
        return read_result_body(path)
    except WriteRefused as exc:
        details = dict(exc.details)
        if details.get("param") == "body_file":
            details["param"] = "notes_file"
        code = "VALIDATION_FAILED" if exc.code == "NOT_FOUND" else exc.code
        message = exc.message.replace("body file", "notes file").replace(
            "result body", "review notes"
        )
        raise WriteRefused(code, message, details) from None


def _review_value(row: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "record": row["record_id"],
        "item": row["item"],
        "commit": row.get("commit"),
        "verdict": row["verdict"],
    }
    data = row.get("data")
    is_contract = (
        isinstance(data, Mapping)
        and data.get("review_contract_digest") is not None
    )
    if is_contract or row.get("result_record") is not None:
        value["result"] = row.get("result_record")
    if is_contract and row.get("path") is not None:
        value["path"] = row.get("path")
    return value


def _required(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"review needs {what}", {"param": what}
        )
    return value.strip()


def _optional_text(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} must not be blank", {"param": what}
        )
    return value.strip()


def _resolve(root: Path, rev: str, what: str) -> str:
    sha = git.resolve_commit(root, rev)
    if sha is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{what} {rev!r} names no commit in this checkout",
            {"param": what, "value": rev},
        )
    return sha


def _free_text(value: Any, what: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > TEXT_CAP:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{what} must be 1 to {TEXT_CAP} characters",
            {"param": what},
        )
    return text


def _notes(path: Any) -> str | None:
    if path is None:
        return None
    try:
        with Path(str(path)).open("rb") as handle:
            raw = handle.read(NOTES_MAX * 4 + 1)
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read the notes file {str(path)!r}: {exc}",
            {"param": "notes_file"},
        ) from exc
    if len(text) > NOTES_MAX:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"review notes are limited to {NOTES_MAX} characters",
            {"param": "notes_file", "limit": NOTES_MAX},
        )
    return text


def verify_review_checks(ctx: Context, item: str, actor: str) -> list[str]:
    """Verify the item's review-mode checks against the verdict just recorded.

    The item gets a review check when it has none, so a verdict always reaches
    the gate through proof the check layer can read. A refusal here never undoes
    the verdict: the check is simply reported as not verified.
    """
    names = [
        str(row["slug"])
        for row in query.check_states(ctx.index, item)
        if row["mode"] == "review"
    ]
    if not names:
        declared = _declare_review_check(ctx, item, actor)
        names = [] if declared is None else [declared]
    verified: list[str] = []
    for name in names:
        try:
            result = checks_handler.verify(
                ctx,
                {
                    "target": name,
                    "worktree": None,
                    "verdict": None,
                    "evidence": [],
                    "confirm": None,
                    "key": None,
                },
                actor,
            )
        except (WriteRefused, ModelError):
            continue
        if any(row.get("check") == name for row in result.get("verdicts", [])):
            verified.append(name)
    return verified


def _declare_review_check(ctx: Context, item: str, actor: str) -> str | None:
    for name in (f"review-{item}", *[f"review-{item}-{n}" for n in range(2, 10)]):
        taken = ctx.index.conn.execute(
            "SELECT 1 FROM docs WHERE slug = ? LIMIT 1", (name,)
        ).fetchone()
        if taken is not None:
            continue
        spec = {"schema": 1, "mode": "review", "acceptance": False, "verifies": [item]}
        spec_path = local_dir(Path(ctx.store)) / "review" / item / "check.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(spec), encoding="utf-8", newline="\n")
        try:
            checks_handler.check(
                ctx,
                {
                    "slug": name,
                    "target": item,
                    "spec_file": str(spec_path),
                    "expect": ABSENT,
                    "key": None,
                },
                actor,
            )
        except (WriteRefused, ModelError):
            return None
        return name
    return None
