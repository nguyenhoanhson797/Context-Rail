"""The end of a merge: the gate over the proofs of the exact candidate, the
fast-forward, the closeout Records and the push the charter may allow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.gateway.handlers.common import (
    bounded,
    check_gate_state,
    read_doc,
    review_gate_state,
)
from context_rail.gateway.handlers.docs import move as move_item
from context_rail.kernel import gates
from context_rail.kernel.lifecycle import category_of
from context_rail.runner import git as git_runner
from context_rail.store import query
from context_rail.store.doc import etag
from context_rail.store.location import owns_path
from context_rail.store.write import WriteRefused, commit, resolve_unique

from .budget import _status_in
from .checkpoint import _checkpoint, _status_entries, _store_paths
from .common import CLOSEOUT_MESSAGE, SUMMARY_MAX

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _merge_gate(
    ctx: Context, *, slug: str, candidate: str, worktree: Path, actor: str
) -> gates.GateDecision:
    """The ``merge`` gate's decision over the proofs of this exact candidate."""
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    checks = [
        check_gate_state(ctx, row, worktree=worktree)
        for row in query.check_states(ctx.index, slug)
    ]
    review = review_gate_state(query.review_for_item(ctx.index, slug))
    return gates.evaluate_gate(
        policy, "merge", slug, checks, review, actor == "human", candidate
    )


def _fast_forward(
    ctx: Context, *, root: Path, candidate: str, observed: str, store_paths: tuple[str, ...]
) -> str:
    """CAS the default HEAD, then fast-forward it onto the candidate only."""
    head_now = git_runner.rev_parse_head(root, root)
    if head_now != observed:
        raise WriteRefused(
            "CONFLICT",
            "the default branch moved while the candidate was being merged",
            {"expected": observed, "actual": head_now},
        )
    foreign = [path for path in _status_entries(root) if not owns_path(path, store_paths)]
    if foreign:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout has changes outside the store, so the fast-forward is refused",
            {"paths": foreign[:20]},
        )
    result = git_runner.fast_forward(root, root, candidate)
    if result["timed_out"] or result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR",
            f"git merge --ff-only failed: {bounded(detail or 'no output', 800)}",
            {"candidate": candidate, "head": head_now},
        )
    merged_head = git_runner.rev_parse_head(root, root)
    if merged_head != candidate:
        raise WriteRefused(
            "PROCESS_ERROR",
            "the fast-forward did not land on the candidate",
            {"candidate": candidate, "head": merged_head},
        )
    return merged_head


def _merge_record(
    ctx: Context,
    *,
    slug: str,
    run: str,
    merge_id: str,
    candidate: str,
    merged_head: str,
    branch: str,
    rebased: dict[str, Any],
    actor: str,
) -> str | None:
    continuation = rebased.get("continuation") or {}
    data = {
        "schema": 1,
        "item": slug,
        "run": run,
        "merge_id": merge_id,
        "candidate_commit": candidate,
        "commit": merged_head,
        "branch": branch,
        "merge_method": "fast-forward",
        "conflict_files": list(rebased.get("conflicts") or ()),
        "continuation_run": continuation.get("run"),
        "continuation_session": continuation.get("session_id"),
        "session_continued": continuation.get("session_continued"),
        "continuation_attempts": 1 if continuation else 0,
    }
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "merge.completed", "about": [slug], "data": data},
        actor=actor,
        index=ctx.index,
    )
    return result.get("record")


def _close_item(
    ctx: Context, *, slug: str, actor: str, commit_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Move the merged Item to its done status through the Item's own gate."""
    rel = resolve_unique(ctx.index, slug)
    data, doc = read_doc(ctx.store, rel)
    if category_of(ctx.pack, doc.status or "") == "done":
        return slug, None
    try:
        move_item(
            ctx,
            {
                "slug": slug,
                "status": _status_in(ctx.pack.statuses, "done"),
                "expect": etag(data),
                "resolution": f"merged {commit_id}",
                "confirm": None,
                "key": None,
            },
            actor,
        )
    except WriteRefused as error:
        return None, {
            "phase": "closeout",
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    return slug, None


def _push_default(
    ctx: Context,
    *,
    root: Path,
    slug: str,
    run: str,
    merge_record: str | None,
    actor: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Commit the closeout, then push the default branch as a normal fast-forward."""
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    if not policy.allow_push:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            "the charter does not allow push",
            {"item": slug, "allow_push": False},
        )
    store = Path(ctx.store)
    closeout = _checkpoint(
        ctx, root, _store_paths(ctx, root), actor, message=CLOSEOUT_MESSAGE
    )
    head = git_runner.rev_parse_head(root, root)
    result = git_runner.push(root, root, policy.remote, policy.default_branch)
    detail = (result["stderr"] or result["stdout"]).strip()
    pushed = result["exit"] == 0 and not result["timed_out"]
    data = {
        "schema": 1,
        "item": slug,
        "run": run,
        "merge": merge_record,
        "remote": policy.remote,
        "branch": policy.default_branch,
        "head": head,
        "closeout": closeout,
        "pushed": pushed,
        "error": None if pushed else bounded(detail or "no output", SUMMARY_MAX),
        "runner_digest": result.get("digest"),
    }
    commit(
        store,
        ctx.manifest,
        [],
        {"kind": "push.result", "about": [slug], "data": data},
        actor=actor,
        index=ctx.index,
    )
    if pushed:
        return True, None
    return False, {
        "remote": policy.remote,
        "branch": policy.default_branch,
        "head": head,
        "reason": data["error"],
    }
