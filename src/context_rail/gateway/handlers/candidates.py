"""Candidate records: the commits an agent, in any tool, produced for an item.

Rail never starts the agent that did the work. The tool commits in whatever
checkout or worktree it likes and tells the rail which commit is the candidate;
the rail resolves it, lists what it changed and records who produced it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.context.budget import item_budget
from context_rail.core.util import now_iso
from context_rail.gateway.handlers.common import checkout, profile_document, skill_pins
from context_rail.gateway.handlers.independence import (
    code_reach_violations,
    independence_refusal,
)
from context_rail.gateway.handlers.reviews import record_review, verify_review_checks
from context_rail.kernel import structure
from context_rail.model.candidate import (
    CANDIDATE_KIND,
    CANDIDATE_SCHEMA,
    DIFF_TEXT_CAP,
    FILES_CAP,
    UNKNOWN_USAGE,
    parse_candidate,
    parse_usage_map,
)
from context_rail.runner import git
from context_rail.store import query
from context_rail.store.lock import store_lock
from context_rail.store.write import WriteRefused, replay
from context_rail.store.write import commit as commit_records

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["record_candidate", "record_review", "verify_review_checks"]

_NUMBER = re.compile(r"^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$")
_REACH_LISTED = 10


def record_candidate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Record one commit as a candidate for an item, or return the record it has."""
    key = args.get("key")
    with store_lock(ctx.store):
        ctx.index.sync()
        saved = replay(ctx.store, key)
        if saved is not None:
            found = query.candidate_by_record(ctx.index, saved.get("record") or "")
            if found is None:
                return {"record": saved.get("record"), "replayed": True}
            return _value(ctx, found, replayed=True)
        return _record(ctx, args, actor, key)


def _record(
    ctx: Context, args: dict[str, Any], actor: str, key: str | None
) -> dict[str, Any]:
    slug = _required(args.get("item"), "item")
    rev = _required(args.get("commit"), "commit")
    given_base = _given(args.get("base"), "base")
    profile_name = _given(args.get("profile"), "profile")
    usage = _usage(args.get("usage"))
    duration = _duration(args.get("duration"))
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    _require_workable(ctx, slug)
    root, _git_dir = checkout(ctx, action="recording a candidate")
    commit = _resolve(root, rev, "commit")
    explicit_base = _resolve(root, given_base, "base") if given_base is not None else None
    profile = profile_document(ctx, profile_name)[2] if profile_name is not None else None
    existing = query.candidate_for_commit(ctx.index, slug, commit)
    if existing is not None:
        return _value(ctx, existing, replayed=True)
    base = explicit_base or _default_base(root, commit, policy.default_branch)
    names = git.diff_names(root, base, commit)

    identity = ctx.identity
    seat = identity.seat if identity.kind == "seat" else None
    role = identity.role if seat is not None else None
    if seat is not None and role is not None:
        _refuse_reach(ctx, seat, role, names)
        if role.independent_of:
            reason = independence_refusal(
                ctx,
                item=slug,
                step_kinds=role.independent_of,
                acting=(seat.seat, profile_name or ""),
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

    diff_snapshot, diff_truncated = git.diff_text(root, base, commit, DIFF_TEXT_CAP)

    payload: dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        "item": slug,
        "commit": commit,
        "base": base,
        "files_changed": names[:FILES_CAP],
        "diff": diff_snapshot,
        "diff_truncated": diff_truncated,
        "attempt": query.candidate_count(ctx.index, slug) + 1,
        "actor": actor,
        "at": now_iso(),
    }
    if len(names) > FILES_CAP:
        payload["files_truncated"] = True
    if seat is not None:
        payload["seat"] = seat.seat
        payload["seat_role"] = seat.role
    if profile_name is not None and profile is not None:
        payload["profile"] = profile_name
        payload["profile_version"] = profile.version
        payload["skills"] = skill_pins(ctx, profile)
    for field in ("model", "harness"):
        if args.get(field) is not None:
            payload[field] = args[field]
    if usage:
        payload["usage"] = usage
    if duration is not None:
        payload["duration_s"] = duration
    parse_candidate(payload)

    result = commit_records(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": CANDIDATE_KIND, "about": [slug], "data": payload},
        actor=actor,
        key=key,
        index=ctx.index,
    )
    stored = query.candidate_by_record(ctx.index, result["record"])
    if stored is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "the candidate was written but is not in the index yet",
            {"record": result["record"]},
        )
    return _value(
        ctx,
        stored,
        replayed=False,
        extra={} if profile is None else profile.budgets,
    )


def _value(
    ctx: Context,
    row: dict[str, Any],
    *,
    replayed: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = row["data"]
    state = item_budget(ctx.index, ctx.policy, row["item"], extra)
    return {
        "record": row["record_id"],
        "item": row["item"],
        "commit": row["commit"],
        "base": row["base"],
        "attempt": row["attempt"],
        "files_changed": list(data.get("files_changed", [])),
        "files_truncated": bool(data.get("files_truncated", False)),
        "replayed": replayed,
        "budget": {
            "state": state.state,
            "reasons": list(state.reasons),
            "unknown": state.unknown,
        },
    }


def _required(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"a candidate needs {what}", {"param": what}
        )
    return value.strip()


def _given(value: Any, what: str) -> str | None:
    if value is None:
        return None
    return _required(value, what)


def _resolve(root: Path, rev: str, what: str) -> str:
    sha = git.resolve_commit(root, rev)
    if sha is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{what} {rev!r} names no commit in this checkout",
            {"param": what, "value": rev},
        )
    return sha


def _default_base(root: Path, commit: str, default_branch: str) -> str:
    """Where the work left the default branch, else the commit's first parent."""
    tip = git.resolve_commit(root, default_branch)
    forked = git.merge_base(root, commit, tip) if tip is not None else None
    if forked is not None and forked != commit:
        return forked
    parent = git.first_parent(root, commit)
    if parent is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "the commit has no parent to compare against; pass --base",
            {"param": "base", "commit": commit},
        )
    return parent


def _refuse_reach(ctx: Context, seat: Any, role: Any, names: list[str]) -> None:
    violations = code_reach_violations(ctx, seat=seat, role=role, files=names)
    if not violations:
        return
    listed = ", ".join(violations[:_REACH_LISTED])
    more = len(violations) - _REACH_LISTED
    raise WriteRefused(
        "BUSINESS_RULE_VIOLATED",
        "the commit changes files outside the seat's code write reach: "
        + listed
        + (f" and {more} more" if more > 0 else ""),
        {
            "reason": "code_reach",
            "paths": violations[:FILES_CAP],
            "reach": [pattern.raw for pattern in role.code_write],
        },
    )


def _usage(raw: Any) -> dict[str, int | float | str]:
    counters: dict[str, int | float | str] = {}
    for entry in raw or ():
        name, separator, text = str(entry).partition("=")
        name, text = name.strip(), text.strip()
        if not separator or not name or not text:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"--usage takes NAME=NUMBER or NAME={UNKNOWN_USAGE}, not {entry!r}",
                {"param": "usage"},
            )
        if name in counters:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"--usage names {name!r} twice",
                {"param": "usage", "counter": name},
            )
        counters[name] = text if text == UNKNOWN_USAGE else _number(text, f"usage {name}")
    return parse_usage_map(counters, "usage")


def _duration(raw: Any) -> int | float | None:
    if raw is None:
        return None
    return _number(str(raw).strip(), "duration")


def _number(text: str, what: str) -> int | float:
    if not _NUMBER.fullmatch(text):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{what} must be a non-negative number, not {text!r}",
            {"param": what},
        )
    return int(text) if text.isdigit() else float(text)


def _require_workable(ctx: Context, slug: str) -> None:
    rows = _item_rows(ctx)
    row = next((found for found in rows if found.slug == slug), None)
    if row is None:
        raise WriteRefused("NOT_FOUND", f"{slug!r} is not an item", {"item": slug})
    held = structure.blocked(rows)
    if slug in structure.workable(rows, held):
        return
    if row.category in structure.FINISHED_CATEGORIES:
        reason = "finished"
    elif slug in held:
        reason = held[slug]
    elif row.container or any(slug in other.part_of for other in rows):
        reason = "container"
    else:
        reason = "not_executable"
    raise WriteRefused(
        "INVALID_STATE",
        f"{slug!r} cannot take a candidate: {reason}",
        {"item": slug, "reason": reason},
    )


def _item_rows(ctx: Context) -> list[structure.ItemRow]:
    index = ctx.index
    edges: dict[str, dict[str, list[str]]] = {}
    for src, rel, dst in index.conn.execute(
        "SELECT r.src, r.rel, r.dst FROM relations r JOIN docs d ON d.path = r.src_path "
        "WHERE d.primitive = 'item' AND d.broken = 0 "
        "AND r.rel IN ('part_of', 'depends_on')"
    ):
        edges.setdefault(src, {}).setdefault(rel, []).append(dst)
    rows = index.conn.execute(
        "SELECT slug, category, status, preset FROM docs "
        "WHERE primitive = 'item' AND broken = 0"
    ).fetchall()
    result: list[structure.ItemRow] = []
    for name, category, status, preset in rows:
        declared = ctx.pack.presets.get(preset or "")
        workflow = ctx.pack.workflows[
            declared.workflow if declared is not None else "default"
        ]
        result.append(
            structure.ItemRow(
                slug=name,
                category=category,
                part_of=tuple(sorted(edges.get(name, {}).get("part_of", ()))),
                depends_on=tuple(sorted(edges.get(name, {}).get("depends_on", ()))),
                last_activity_ms=None,
                queued=workflow.queued(status or ""),
                preset=preset or None,
                workflow=workflow.name,
                container=declared.container if declared is not None else None,
                status=status,
            )
        )
    return result