"""Narrow result writer: record one workflow-shaped Markdown result per attempt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso
from context_rail.gateway.handlers.common import (
    parse_set_values,
    prepare_document_fields,
    preset_for,
    skill_pins_for,
)
from context_rail.gateway.handlers.independence import independence_refusal
from context_rail.model.compiler import CompiledArtifact
from context_rail.model.errors import ModelError
from context_rail.model.results import (
    RESULT_BODY_MAX_BYTES,
    RESULT_KIND,
    RESULT_OUTCOMES,
    RESULT_SCHEMA,
    normalize_result_body,
    parse_result,
    result_body_hash,
    validate_result_body,
)
from context_rail.store import query
from context_rail.store.doc import parse_doc
from context_rail.store.location import PathEscape, confine
from context_rail.store.lock import store_lock
from context_rail.store.results import item_content_digest, work_cycle
from context_rail.store.write import WriteRefused, replay
from context_rail.store.write import commit as commit_records

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["artifact_fields", "read_result_body", "record_result"]

_MAX_INPUT_BYTES = RESULT_BODY_MAX_BYTES + 1
_TEXT_CAP = 200
_RESERVED_CALLER_FIELDS = frozenset(
    {
        "workflow_name",
        "item",
        "attempt",
        "actor",
        "at",
        "outcome",
        "candidate_commit",
        "body_hash",
        "item_digest",
        "work_cycle",
        "contract_digest",
        "seat",
        "seat_role",
        "profile",
        "profile_version",
        "skills",
        "model",
        "harness",
    }
)


def read_result_body(path: Path) -> str:
    """Read at most ``RESULT_BODY_MAX_BYTES + 1`` bytes, decode UTF-8, and normalize."""
    target = Path(path)
    try:
        with target.open("rb") as handle:
            raw = handle.read(_MAX_INPUT_BYTES)
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND",
            f"body file {str(target)!r} does not exist",
            {"param": "body_file", "path": str(target)},
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read body file {str(target)!r}: {exc}",
            {"param": "body_file", "path": str(target)},
        ) from exc

    if len(raw) > RESULT_BODY_MAX_BYTES:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"result body exceeds {RESULT_BODY_MAX_BYTES} bytes",
            {"param": "body_file", "limit": RESULT_BODY_MAX_BYTES},
        )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"body file {str(target)!r} is not valid UTF-8",
            {"param": "body_file", "path": str(target)},
        ) from None
    return normalize_result_body(decoded)


def artifact_fields(
    artifact: CompiledArtifact,
    workflow: str,
    raw: Sequence[str],
    *,
    preset_obj: Any = None,
) -> dict[str, Any]:
    """Merge caller ``--set KEY=VALUE`` metadata/defaults with engine-owned ``workflow_name``."""
    raw_list = list(raw)
    seen_keys: set[str] = set()
    for entry in raw_list:
        key, sep, _ = str(entry).partition("=")
        key = key.strip()
        if not sep or not key:
            raise WriteRefused("VALIDATION_FAILED", f"{entry!r} is not KEY=VALUE")
        if key in seen_keys:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"--set names {key!r} more than once",
                {"key": key},
            )
        seen_keys.add(key)
        if key in _RESERVED_CALLER_FIELDS:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{key!r} is owned by the engine and cannot be set via --set",
                {"key": key},
            )
    caller_fields = parse_set_values(raw_list)
    caller_fields["workflow_name"] = workflow
    if preset_obj is None:
        from context_rail.model.pack import Preset

        preset_obj = Preset(
            name=artifact.preset,
            primitive="record",
            tags=(),
            fields=dict(artifact.fields),
            template=artifact.template,
            required=tuple(artifact.required),
            defaults=dict(artifact.defaults),
        )
    return prepare_document_fields(preset_obj, caller_fields)


def record_result(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Record one workflow-shaped Markdown producer result for an item."""
    key = args.get("key")
    with store_lock(ctx.store):
        ctx.index.sync()
        saved = replay(ctx.store, key)
        if saved is not None:
            record_id = saved.get("record") or ""
            found = query.result_by_record(ctx.index, record_id) if record_id else None
            if found is None:
                return {"record": saved.get("record"), "replayed": True}
            return _receipt(found, replayed=True)
        return _record(ctx, args, actor, key)


def _record(
    ctx: Context, args: dict[str, Any], actor: str, key: str | None
) -> dict[str, Any]:
    slug = _required_text(args.get("item"), "item")
    body_arg = _required_text(args.get("body_file"), "body_file")
    outcome = _outcome(args.get("outcome"))
    commit_arg = _optional_text(args.get("commit"), "commit")
    model_name = _optional_free_text(args.get("model"), "model")
    harness = _optional_free_text(args.get("harness"), "harness")

    body_path = _resolve_external_body_path(ctx.store, body_arg)

    item_row = _resolve_item(ctx, slug)
    item_path, workflow_name = item_row

    compiled_protocol = (getattr(ctx.model, "results", {}) or {}).get(workflow_name)
    if compiled_protocol is None:
        raise WriteRefused(
            "INVALID_STATE",
            f"workflow {workflow_name!r} has no declared result contract; adopt results in the pack first",
            {"reason": "no_result_contract", "item": slug, "workflow": workflow_name},
        )
    artifact = compiled_protocol.produce
    preset_obj = preset_for(ctx.pack, artifact.preset)
    fields = artifact_fields(
        artifact, workflow_name, args.get("set") or (), preset_obj=preset_obj
    )

    body = read_result_body(body_path)
    try:
        validate_result_body(body, artifact.sections)
    except ModelError as exc:
        raise WriteRefused(exc.code, exc.message, exc.details) from None
    body_hash = result_body_hash(body)

    identity = ctx.identity
    seat = identity.seat if identity.kind == "seat" else None
    role = identity.role if seat is not None else None
    profile = ctx.profile

    if seat is not None and role is not None and role.independent_of:
        reason = independence_refusal(
            ctx,
            item=slug,
            step_kinds=role.independent_of,
            acting=(seat.seat, seat.profile or ""),
        )
        if reason is not None:
            raise WriteRefused(
                "PERMISSION_DENIED",
                reason,
                {
                    "reason": "independence_violation",
                    "item": slug,
                    "steps": list(role.independent_of),
                },
            )

    candidate_commit = _resolve_candidate_commit(
        ctx, slug, commit_arg, actor=actor, seat=seat
    )

    attempt = query.result_count(ctx.index, slug) + 1
    item_digest = item_content_digest(ctx.store, item_path)
    cycle = work_cycle(ctx, slug)
    at = now_iso()

    payload: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "item": slug,
        "attempt": attempt,
        "actor": actor,
        "at": at,
        "outcome": outcome,
        "candidate_commit": candidate_commit,
        "body_hash": body_hash,
        "item_digest": item_digest,
        "work_cycle": cycle,
        "contract_digest": artifact.digest,
    }
    if seat is not None:
        payload["seat"] = seat.seat
        payload["seat_role"] = seat.role
        if seat.profile is not None and profile is not None:
            payload["profile"] = seat.profile
            payload["profile_version"] = profile.version
            if role is not None and role.skills:
                payload["skills"] = skill_pins_for(ctx, role.skills)
            elif profile.skills:
                from context_rail.gateway.handlers.common import skill_pins

                payload["skills"] = skill_pins(ctx, profile)
    elif profile is not None and ctx.actor_named:
        payload["profile"] = actor
        payload["profile_version"] = profile.version
        if profile.skills:
            from context_rail.gateway.handlers.common import skill_pins

            payload["skills"] = skill_pins(ctx, profile)

    if model_name is not None:
        payload["model"] = model_name
    if harness is not None:
        payload["harness"] = harness

    try:
        parse_result(payload)
    except ModelError as exc:
        raise WriteRefused(exc.code, exc.message, exc.details) from None

    title = f"{slug} result {attempt}"
    record_req = {
        "preset": artifact.preset,
        "kind": RESULT_KIND,
        "about": [slug],
        "title": title,
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
    stored = query.result_by_record(ctx.index, committed["record"])
    if stored is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "the result was written but is not in the index yet",
            {"record": committed["record"]},
        )
    return _receipt(stored, replayed=False)


def _receipt(row: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
    data = row.get("data") or {}
    return {
        "record": row["record_id"],
        "path": row["path"],
        "item": row["item"],
        "attempt": data.get("attempt"),
        "outcome": row["outcome"],
        "candidate_commit": row["candidate_commit"],
        "body_hash": row["body_hash"],
        "replayed": replayed,
    }


def _resolve_external_body_path(store: Path, raw: str) -> Path:
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot resolve body file {raw!r}: {exc}",
            {"param": "body_file", "path": raw},
        ) from exc
    store_root = Path(store).resolve()
    if resolved == store_root or resolved.is_relative_to(store_root):
        raise WriteRefused(
            "FORBIDDEN",
            f"body file {raw!r} must be outside the store",
            {"param": "body_file", "path": raw},
        )
    if resolved.is_dir():
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"body file {raw!r} is a directory",
            {"param": "body_file", "path": raw},
        )
    return resolved


def _resolve_item(ctx: Context, slug: str) -> tuple[str, str]:
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
    # Read full item document to ensure it is readable inside store.
    try:
        raw = confine(ctx.store, path).read_bytes()
    except (OSError, PathEscape) as exc:
        raise WriteRefused("NOT_FOUND", f"item {slug!r} cannot be read: {exc}", {"item": slug}) from exc
    doc = parse_doc(path, raw)
    if doc.problems:
        raise WriteRefused(
            "INVALID_STATE",
            f"item {slug!r} has document problems: " + ", ".join(doc.problems),
            {"item": slug},
        )
    return path, workflow or "default"


def _resolve_candidate_commit(
    ctx: Context,
    item: str,
    commit_arg: str | None,
    *,
    actor: str,
    seat: Any,
) -> str | None:
    if commit_arg is None:
        return None
    needle = commit_arg.strip()
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
    sha = unique_shas[0]
    candidate = query.candidate_for_commit(ctx.index, item, sha)
    assert candidate is not None
    cand_actor = candidate.get("actor")
    cand_seat = candidate.get("seat")
    if seat is not None:
        same_producer = cand_seat == seat.seat or (
            cand_seat is None and cand_actor == actor
        )
    else:
        same_producer = cand_seat is None and cand_actor == actor
    if not same_producer:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"candidate {sha[:12]} belongs to producer {cand_seat or cand_actor!r}, not {seat.seat if seat else actor!r}",
            {"reason": "producer_mismatch", "item": item, "commit": sha},
        )
    return sha


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"result needs {name}", {"param": name}
        )
    return value.strip()


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"{name} must not be blank", {"param": name}
        )
    return value.strip()


def _optional_free_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > _TEXT_CAP:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{name} must be 1 to {_TEXT_CAP} characters",
            {"param": name},
        )
    return text


def _outcome(raw: Any) -> str:
    if raw is None:
        return "completed"
    if not isinstance(raw, str) or not raw.strip():
        raise WriteRefused(
            "VALIDATION_FAILED",
            "outcome must be one of " + ", ".join(RESULT_OUTCOMES),
            {"param": "outcome"},
        )
    value = raw.strip()
    if value not in RESULT_OUTCOMES:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"unknown outcome {value!r}; expected one of " + ", ".join(RESULT_OUTCOMES),
            {"param": "outcome"},
        )
    return value
