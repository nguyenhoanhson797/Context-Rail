"""Rebase one approved candidate onto the default branch and fast-forward it, with
one conflict continuation, the closeout and the optional push."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.core.util import new_ulid
from context_rail.gateway.handlers.common import bounded, checkout, profile_document, read_doc, required
from context_rail.kernel.lifecycle import category_of
from context_rail.runner import dispatch as local_dispatch, git as git_runner, merge as local_merge
from context_rail.runner.droid import droid_profile
from context_rail.store import query
from context_rail.store.write import WriteRefused, resolve_unique

from .budget import _effective_budget, _run_facts
from .checkpoint import _checkpoint, _current_branch, _store_paths
from .closeout import _close_item, _fast_forward, _merge_gate, _merge_record, _push_default
from .common import BUILTIN_SECONDS, IMPL_ROLE, MERGE_CHECKPOINT_MESSAGE, REVIEW_ROLE, _refuse_pending, _resolve_item, _verify_worktree
from .conflict import _continue_conflict, _merge_escalated
from .review import _review_candidate, _separation_reason

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def merge(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Rebase one approved candidate onto the default branch and fast-forward it.

    The gate is the whole point: a fresh passing Check set and a separated
    approval bound to the exact commit that lands. A rebase changes that commit,
    so the Checks and the review run again before the fast-forward, and a
    conflict gets exactly one implementer continuation before the merge is
    escalated with its worktree left for inspection.
    """
    store = Path(ctx.store)
    item = required(args.get("item"), "an Item slug", action="merge")
    implementation_run = required(args.get("run"), "a verified run id", action="merge")
    expect = required(args.get("expect"), "the Item's etag", action="merge")
    key = required(args.get("key"), "a merge key", action="merge")
    push = bool(args.get("push"))

    replayed = _merge_replayed(store, key, item, implementation_run, expect)
    if replayed is not None:
        return replayed

    index = ctx.index
    slug, rel, current = _resolve_item(ctx, item)
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    if push and not policy.allow_push:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            "the charter does not allow push",
            {"item": slug, "allow_push": False, "remote": policy.remote},
        )
    if current != expect:
        raise WriteRefused(
            "CONFLICT",
            f"{rel!r} is at etag {current}, not {expect}",
            {"path": rel, "expected": expect, "actual": current},
        )
    run = query.run_record(index, implementation_run)
    if run is None or run["item"] != slug:
        raise WriteRefused(
            "INVALID_STATE",
            "no completed run of this Item carries that id",
            {"item": slug, "run": implementation_run},
        )
    if run["role"] != IMPL_ROLE:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"run {implementation_run!r} is a {run['role']!r}, not an implementer run",
            {"run": implementation_run, "role": run["role"]},
        )
    if not query.check_states(index, slug):
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            "the Item declares no Checks, so the merge gate never opens",
            {"item": slug, "gate": "merge"},
        )
    candidate = run["candidate_commit"]
    if run["status"] != "verified" or not candidate:
        raise WriteRefused(
            "INVALID_STATE",
            "only a verified candidate can be merged",
            {"run": implementation_run, "status": run["status"]},
        )

    root, _git_dir = checkout(ctx, action="a dispatch")
    branch = _current_branch(root)
    if branch != policy.default_branch:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout is not on its approved default branch",
            {"current": branch, "expected": policy.default_branch},
        )
    worktree, candidate_branch = _candidate_worktree(store, run, candidate)
    merge_id = new_ulid()

    reconciled = _reconcile_merge(
        ctx,
        slug=slug,
        run=implementation_run,
        candidate=candidate,
        branch=branch,
        push=push,
        actor=actor,
    )
    if reconciled is not None:
        _save_merge(
            store,
            merge_id,
            key=key,
            item=item,
            run=implementation_run,
            expect=expect,
            result=reconciled,
        )
        return reconciled

    _data, doc = read_doc(store, rel)
    if category_of(ctx.pack, doc.status or "") in ("done", "dropped"):
        raise WriteRefused(
            "INVALID_STATE",
            f"{slug!r} is already {doc.status!r}",
            {"item": slug, "status": doc.status},
        )

    store_paths = _store_paths(ctx, root)
    review = _review_before_merge(ctx, slug)
    _checkpoint(ctx, root, store_paths, actor, message=MERGE_CHECKPOINT_MESSAGE)
    observed = git_runner.rev_parse_head(root, root)

    with local_merge.acquire_merge_lock(store, slug):
        _refuse_pending(store, slug)
        if git_runner.rebase_in_progress(worktree, worktree):
            raise WriteRefused(
                "INVALID_STATE",
                "a rebase is already in progress in the candidate worktree; inspect it first",
                {"item": slug, "worktree": str(worktree), "branch": candidate_branch},
            )
        rebased = _rebase_candidate(
            ctx,
            slug=slug,
            rel=rel,
            run=run,
            worktree=worktree,
            onto=observed,
            store=store,
            merge_id=merge_id,
            actor=actor,
        )

    if rebased["escalation"] is not None:
        value = _merge_escalated(
            ctx,
            slug=slug,
            rel=rel,
            run=implementation_run,
            merge_id=merge_id,
            candidate=candidate,
            branch=candidate_branch,
            worktree=worktree,
            rebased=rebased,
            actor=actor,
        )
        _save_merge(
            store,
            merge_id,
            key=key,
            item=item,
            run=implementation_run,
            expect=expect,
            result=value,
        )
        return value

    final = rebased["candidate"]
    if final != candidate or review is None:
        verified = _verify_worktree(ctx, slug, worktree, actor)
        if not verified["passed"]:
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                "the rebased candidate does not pass its Checks",
                {
                    "item": slug,
                    "candidate": final,
                    "reason": verified.get("error") or verified.get("reason"),
                    "verdicts": [
                        row["record"] for row in verified["verdicts"] if row.get("record")
                    ],
                },
            )
        reviewed = _review_for_merge(
            ctx,
            slug=slug,
            rel=rel,
            item=item,
            run=run,
            candidate=final,
            actor=actor,
            merge_id=merge_id,
        )
        if reviewed["verdict"] != "approve":
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                "the separated review did not approve the rebased candidate",
                {
                    "item": slug,
                    "candidate": final,
                    "verdict": reviewed["verdict"],
                    "review": reviewed["review"],
                },
            )
        review = _review_before_merge(ctx, slug)

    decision = _merge_gate(ctx, slug=slug, candidate=final, worktree=worktree, actor=actor)
    if not decision.allowed:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            "the merge gate is not satisfied",
            {"gate": "merge", "item": slug, "reasons": list(decision.reasons)},
        )

    with local_merge.acquire_merge_lock(store, slug):
        merged_head = _fast_forward(
            ctx,
            root=root,
            candidate=final,
            observed=observed,
            store_paths=store_paths,
        )
        record = _merge_record(
            ctx,
            slug=slug,
            run=implementation_run,
            merge_id=merge_id,
            candidate=final,
            merged_head=merged_head,
            branch=branch,
            rebased=rebased,
            actor=actor,
        )
        closed, closeout = _close_item(ctx, slug=slug, actor=actor, commit_id=final)
        value: dict[str, Any] = {
            "commit": merged_head,
            "branch": branch,
            "merged": True,
            "pushed": False,
            "closed": closed,
            "record": record,
            "escalation": None,
        }
        if closeout is not None:
            value["closeout"] = closeout
        if push:
            pushed, pending = _push_default(
                ctx,
                root=root,
                slug=slug,
                run=implementation_run,
                merge_record=record,
                actor=actor,
            )
            head_now = git_runner.rev_parse_head(root, root)
            value.update({"pushed": pushed, "commit": head_now})
            if pending is not None:
                value["pending_push"] = pending
        _save_merge(
            store,
            merge_id,
            key=key,
            item=item,
            run=implementation_run,
            expect=expect,
            result=value,
        )
    return value


def _candidate_worktree(
    store: Path, run: dict[str, Any], candidate: str
) -> tuple[Path, str]:
    """The worktree and branch holding the candidate, proven to be that commit."""
    dispatch_id = run.get("dispatch_id")
    progress = (
        local_dispatch.read_progress(store, str(dispatch_id)) if dispatch_id else None
    ) or {}
    reported = progress.get("worktree")
    worktree = Path(str(reported)) if reported else None
    if worktree is None or not worktree.is_dir():
        raise WriteRefused(
            "INVALID_STATE",
            "the candidate worktree is gone; dispatch or restore it before merging",
            {"run": run.get("record_id"), "worktree": reported},
        )
    head = git_runner.rev_parse_head(worktree, worktree)
    if head != candidate:
        raise WriteRefused(
            "INVALID_STATE",
            "the candidate commit changed after its review, so the approval no longer binds",
            {"run": run.get("record_id"), "candidate": candidate, "worktree_head": head},
        )
    return worktree, str(progress.get("branch") or "")


def _review_before_merge(ctx: Context, slug: str) -> dict[str, Any] | None:
    """The newest review of the Item, refused when it blocks the candidate."""
    review = query.review_for_item(ctx.index, slug)
    if review is None:
        return None
    if review["verdict"] != "approve":
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            "the separated review rejected this candidate",
            {
                "item": slug,
                "review": review["record_id"],
                "verdict": review["verdict"],
                "candidate_commit": review["candidate_commit"],
            },
        )
    return review


def _reconcile_merge(
    ctx: Context,
    *,
    slug: str,
    run: str,
    candidate: str,
    branch: str,
    push: bool,
    actor: str,
) -> dict[str, Any] | None:
    """The recorded merge when the candidate already reached the default branch.

    A retry must reconcile the observed git HEAD first: when the candidate is
    already contained in it, the merge happened, so nothing is rebased, closed
    or recorded twice.
    """
    root, _git_dir = checkout(ctx, action="a dispatch")
    observed = git_runner.rev_parse_head(root, root)
    if not git_runner.is_ancestor(root, root, candidate, observed):
        return None
    recorded = _merge_record_for(ctx, slug, run)
    closed = slug if _category(ctx, slug) == "done" else None
    if recorded is None:
        value: dict[str, Any] = {
            "commit": observed,
            "branch": branch,
            "merged": True,
            "pushed": False,
            "closed": closed,
            "record": None,
            "escalation": None,
        }
    else:
        data = recorded["data"]
        value = {
            "commit": data.get("commit") or observed,
            "branch": data.get("branch") or branch,
            "merged": True,
            "pushed": False,
            "closed": closed,
            "record": recorded["id"],
            "escalation": None,
        }
    if push:
        pushed, pending = _push_default(
            ctx,
            root=root,
            slug=slug,
            run=run,
            merge_record=value["record"],
            actor=actor,
        )
        head_now = git_runner.rev_parse_head(root, root)
        value.update({"pushed": pushed, "commit": head_now})
        if pending is not None:
            value["pending_push"] = pending
    return value


def _merge_record_for(
    ctx: Context, slug: str, run: str
) -> dict[str, Any] | None:
    """The newest ``merge.completed`` Record of one run, if any."""
    for record in query.records_about(ctx.index, slug, limit=200):
        if record.get("kind") != "merge.completed":
            continue
        if (record.get("data") or {}).get("run") == run:
            return record
    return None


def _category(ctx: Context, slug: str) -> str | None:
    rel = resolve_unique(ctx.index, slug)
    _data, doc = read_doc(ctx.store, rel)
    return category_of(ctx.pack, doc.status or "")


def _rebase_candidate(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    run: dict[str, Any],
    worktree: Path,
    onto: str,
    store: Path,
    merge_id: str,
    actor: str,
) -> dict[str, Any]:
    """Rebase the candidate onto ``onto``, with one continuation for a conflict."""
    result = git_runner.rebase(worktree, worktree, onto)
    if result["exit"] == 0 and not result["timed_out"]:
        return {
            "candidate": git_runner.rev_parse_head(worktree, worktree),
            "conflicts": (),
            "continuation": None,
            "escalation": None,
        }
    conflicts = git_runner.unmerged_paths(worktree, worktree)
    if not conflicts:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR",
            f"git rebase failed: {bounded(detail or 'no output', 800)}",
            {"item": slug, "onto": onto, "worktree": str(worktree)},
        )
    continuation = _continue_conflict(
        ctx,
        slug=slug,
        rel=rel,
        run=run,
        worktree=worktree,
        onto=onto,
        conflicts=conflicts,
        store=store,
        merge_id=merge_id,
        actor=actor,
    )
    if continuation["resolved"]:
        return {
            "candidate": git_runner.rev_parse_head(worktree, worktree),
            "conflicts": tuple(conflicts),
            "continuation": continuation,
            "escalation": None,
        }
    return {
        "candidate": run["candidate_commit"],
        "conflicts": tuple(conflicts),
        "continuation": continuation,
        "escalation": {
            "reason": continuation["reason"],
            "worktree": str(worktree),
            "conflict_files": list(conflicts),
        },
    }


def _review_for_merge(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    item: str,
    run: dict[str, Any],
    candidate: str,
    actor: str,
    merge_id: str,
) -> dict[str, Any]:
    """Run one separated reviewer at the commit the merge produced."""
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    _profile_rel, profile_etag, profile = profile_document(ctx, REVIEW_ROLE)
    separation = _separation_reason(
        policy,
        implementer=str(run["actor"] or ""),
        implementer_model=str(run["model"] or ""),
        reviewer=REVIEW_ROLE,
        reviewer_model=profile.model,
        invoking=actor,
    )
    if separation is not None:
        raise separation
    droid = droid_profile(profile, name=REVIEW_ROLE)
    budget = _effective_budget(policy.budgets.get("item") or {}, profile.budgets)
    _facts, used_seconds, _attempts = _run_facts(ctx.index, slug)
    remaining = float(budget.seconds or BUILTIN_SECONDS) - used_seconds
    if remaining <= 0:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            f"{slug!r} has no review time left: {used_seconds:g} of {budget.seconds:g} seconds used",
            {"item": slug, "used_seconds": used_seconds},
        )
    return _review_candidate(
        ctx,
        store=Path(ctx.store),
        slug=slug,
        rel=rel,
        item=item,
        implementation_run=str(run["record_id"]),
        candidate=candidate,
        run=run,
        profile_name=REVIEW_ROLE,
        profile=profile,
        profile_etag=profile_etag,
        droid=droid,
        actor=actor,
        key=f"merge:{merge_id}:{candidate[:12]}",
        timeout=remaining,
    )


def _merge_replayed(
    store: Path, key: str, item: str, run: str, expect: str
) -> dict[str, Any] | None:
    """The stored result one merge key already earned, or a refusal.

    The Item's etag is recorded but never compared: closing the Item is part of
    what the merge does, so a retry arrives with a newer etag by design.
    """
    binding = local_merge.read_merge_binding(store, key)
    if binding is None:
        return None
    if binding.get("corrupt"):
        raise WriteRefused(
            "CONFLICT",
            "the merge key binding is unreadable; inspect it before retrying",
            {"key": key},
        )
    if (binding.get("item"), binding.get("run")) != (item, run):
        raise WriteRefused(
            "CONFLICT",
            "this key already names a different merge payload",
            {
                "key": key,
                "item": binding.get("item"),
                "run": binding.get("run"),
                "expect": binding.get("expect"),
                "merge_id": binding.get("merge_id"),
            },
        )
    merge_id = str(binding.get("merge_id"))
    progress = local_merge.read_merge_progress(store, merge_id) or {}
    if progress.get("state") == "completed" and isinstance(progress.get("result"), dict):
        return progress["result"]
    raise WriteRefused(
        "CONFLICT",
        "a merge with this key has not completed; inspect the pending merge",
        {
            "key": key,
            "merge_id": merge_id,
            "phase": progress.get("phase", "unknown"),
        },
    )


def _save_merge(
    store: Path,
    merge_id: str,
    *,
    key: str,
    item: str,
    run: str,
    expect: str,
    result: dict[str, Any],
) -> None:
    """Bind the key and mark the attempt complete, so a replay is exact."""
    local_merge.write_merge_binding(
        store,
        key,
        item=item,
        run=run,
        expect=expect,
        merge_id=merge_id,
    )
    local_merge.write_merge_progress(
        store,
        merge_id,
        item=item,
        run=run,
        state="completed",
        phase="completed",
        result=result,
    )
