"""Rollback: restore retained skill or profile text into a new version."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso
from context_rail.gateway.confirmation import (
    confirmation_phrase,
    validate_confirmation,
)
from context_rail.gateway.handlers.common import (
    VERSIONED_KINDS,
    compose_doc,
    doc_frontmatter,
    document_version,
    read_doc,
    slug_of_path,
)
from context_rail.kernel.rights import ChangeFacts, decision_class
from context_rail.store.doc import etag, parse_doc
from context_rail.store.measure import active_dispatches
from context_rail.store.versions import (
    TraversalRefused,
    canonical_target,
    prior_version_content,
)
from context_rail.store.write import Op, WriteRefused, commit, replay

if TYPE_CHECKING:  # imported lazily by the gateway only when rollback runs
    from context_rail.gateway.gateway import Context

PROTECTED_KEYS = (
    "allowed_tools",
    "budgets",
    "budget",
    "tools",
    "model",
    "effort",
    "auto",
    "allowed_methods",
    "acceptance",
    "gates",
)
_ACCEPTANCE = re.compile(r"^#{1,6}\s*acceptance\s*$", re.IGNORECASE | re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)


def rollback(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Append a new version whose text came from an earlier retained version."""
    store = ctx.store
    saved = replay(store, args.get("key"))
    if saved is not None:
        return _replayed(store, args, saved)
    rel = canonical_target(args["target_class"], args["target"])
    kind = args["target_class"]
    version = args["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise WriteRefused(
            "VALIDATION_FAILED", "rollback version must be a positive integer"
        )
    current_data, current_doc = read_doc(store, rel)
    current_etag = etag(current_data)
    if current_etag != args["expect"]:
        raise WriteRefused(
            "CONFLICT",
            f"{rel!r} is at etag {current_etag}, not {args['expect']}",
            {"path": rel, "expected": args["expect"], "actual": current_etag},
        )
    prior = prior_version_content(ctx.index, rel, version)
    profile = _profile_name(rel) if kind == "profile" else None
    skill = rel if kind == "skill" else None
    if active_dispatches(ctx.index, profile=profile, skill=skill):
        raise WriteRefused(
            "INVALID_STATE",
            "an active run still reads this target",
            {"target": rel},
        )

    current_front = doc_frontmatter(current_doc)
    current_version = document_version(current_front, rel)
    prior_doc = parse_doc(rel, prior.text.encode("utf-8"))
    if prior_doc.problems:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the retained text of {rel!r} does not parse",
            {"path": rel, "problems": list(prior_doc.problems)},
        )
    front = doc_frontmatter(prior_doc)
    for key in ("name", "primitive", "preset", "title"):
        if key in current_front:
            front[key] = current_front[key]
    front["version"] = current_version + 1
    front["updated"] = now_iso()
    after_data = compose_doc(front, prior_doc.body)
    after_text = after_data.decode("utf-8")
    before_text = current_data.decode("utf-8")

    protected = _protected(current_front, front, current_doc.body, prior_doc.body)
    facts = ChangeFacts(
        method="rollback",
        protected=protected,
        acceptance=protected,
        decision_id=None,
        budget_increase=False,
    )
    decision = (
        decision_class("rollback", facts, ctx.policy)
        if ctx.policy is not None
        else ("human-only" if protected else "cto-alone")
    )
    if decision == "human-only":
        if actor != "human":
            raise WriteRefused(
                "PERMISSION_DENIED",
                "restoring a protected target is a human-only action",
                {"target": rel, "actor": actor},
            )
        slug = slug_of_path(rel)
        reason = validate_confirmation("approve", slug, args.get("confirm"))
        if reason is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"target": slug, "confirm": confirmation_phrase("approve", slug)},
            )

    record = {
        "kind": VERSIONED_KINDS[kind],
        "about": [slug_of_path(rel)],
        "data": {
            "schema": 1,
            "target_class": kind,
            "target": rel,
            "before": current_version,
            "after": current_version + 1,
            "before_etag": current_etag,
            "after_etag": etag(after_data),
            "before_text": before_text,
            "after_text": after_text,
            "before_digest": hashlib.sha256(before_text.encode("utf-8")).hexdigest(),
            "after_digest": hashlib.sha256(after_text.encode("utf-8")).hexdigest(),
            "reason": "rollback",
            "reports": [],
            "restored_from_version": version,
        },
    }
    result = commit(
        store,
        ctx.manifest,
        [Op(rel, after_data, current_etag)],
        record,
        actor=actor,
        key=args.get("key"),
        index=ctx.index,
    )
    return {
        "target": rel,
        "version": version,
        "restored_from_version": version,
        "record": result["record"],
        "etag": etag(after_data),
    }


def _protected(
    current: dict[str, Any],
    restored: dict[str, Any],
    current_body: str,
    restored_body: str,
) -> bool:
    if any(current.get(key) != restored.get(key) for key in PROTECTED_KEYS):
        return True
    return _acceptance(current_body) != _acceptance(restored_body)


def _acceptance(body: str) -> str:
    match = _ACCEPTANCE.search(body)
    if match is None:
        return ""
    start = match.end()
    following = _HEADING.search(body, start)
    return body[start : following.start() if following is not None else len(body)].strip()


def _profile_name(rel: str) -> str:
    path = PurePosixPath(rel)
    if path.name == "profile.md":
        return path.parent.name
    return path.stem


def _replayed(store: Any, args: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    record_id = saved.get("record")
    version = args["version"]
    rel = canonical_target(args["target_class"], args["target"])
    data = _record_payload(store, record_id)
    path = store / rel
    return {
        "target": rel,
        "version": version,
        "restored_from_version": data.get("restored_from_version", version),
        "record": record_id,
        "etag": etag(path.read_bytes()) if path.is_file() else None,
    }


def _record_payload(store: Any, record_id: Any) -> dict[str, Any]:
    import json

    if not isinstance(record_id, str) or not record_id:
        return {}
    for path in sorted((store / "records").rglob(f"{record_id}.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            return data
    return {}


__all__ = ["rollback", "TraversalRefused"]
