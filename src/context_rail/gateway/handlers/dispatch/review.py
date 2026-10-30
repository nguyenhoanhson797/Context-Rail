"""One separated reviewer on a verified candidate: its own worktree at the exact
commit, and the replay a retried review key is answered with."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.core.util import new_ulid, now_iso, sha256_hex
from context_rail.gateway.handlers.common import checkout, profile_document, required
from context_rail.kernel.rights import review_separation
from context_rail.model.cto import Profile
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner.droid import (
    DroidProfile,
    droid_profile,
    run_droid,
    write_order_file,
    write_role_file,
)
from context_rail.store import query
from context_rail.store.write import WriteRefused

from .budget import _effective_budget, _run_facts
from .checkpoint import _create_worktree
from .common import (
    BUILTIN_SECONDS,
    IMPL_ROLE,
    REVIEW_ROLE,
    _optional,
    _refuse_pending,
    _resolve_item,
)
from .review_order import _review_metadata, _review_outcome, _review_sections
from .review_records import (
    _candidate_checks_pass,
    _evaluation_record,
    _review_report,
    _review_run_record,
    _review_verdict_record,
    _verdict_ids,
    _verify_review_check,
)

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def review(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Run one separated reviewer on a verified candidate and record its verdict.

    The reviewer reads the candidate commit in a private worktree of its own;
    the implementer's working tree is never opened. The kernel refuses a
    reviewer that is the implementer or, when the charter says so, uses the
    implementer's model, and both refusals happen before any launch.
    """
    store = Path(ctx.store)
    item = required(args.get("item"), "an Item slug", action="dispatch")
    implementation_run = required(args.get("run"), "a verified run id", action="dispatch")
    key = required(args.get("key"), "a review key", action="dispatch")
    profile_name = _optional(args.get("profile")) or REVIEW_ROLE

    replayed = _review_replayed(store, key, item, implementation_run, profile_name)
    if replayed is not None:
        return replayed

    index = ctx.index
    slug, rel, _current = _resolve_item(ctx, item)
    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
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
    candidate = run["candidate_commit"]
    if run["status"] != "verified" or not candidate:
        raise WriteRefused(
            "INVALID_STATE",
            "only a verified candidate can be reviewed",
            {"run": implementation_run, "status": run["status"]},
        )

    _profile_rel, profile_etag, profile = profile_document(ctx, profile_name)
    separation = _separation_reason(
        policy,
        implementer=str(run["actor"] or ""),
        implementer_model=str(run["model"] or ""),
        reviewer=profile_name,
        reviewer_model=profile.model,
        invoking=actor,
    )
    if separation is not None:
        raise separation
    if profile.recipe != REVIEW_ROLE:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"profile {profile_name!r} is a {profile.recipe!r}, not a reviewer",
            {"profile": profile_name, "recipe": profile.recipe},
        )
    droid = droid_profile(profile, name=profile_name)
    budget = _effective_budget(policy.budgets.get("item") or {}, profile.budgets)
    _facts, used_seconds, _used_attempts = _run_facts(index, slug)
    remaining = float(budget.seconds or BUILTIN_SECONDS) - used_seconds
    if remaining <= 0:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            f"{slug!r} has no review time left: {used_seconds:g} of {budget.seconds:g} seconds used",
            {"item": slug, "used_seconds": used_seconds},
        )

    return _review_candidate(
        ctx,
        store=store,
        slug=slug,
        rel=rel,
        item=item,
        implementation_run=implementation_run,
        candidate=candidate,
        run=run,
        profile_name=profile_name,
        profile=profile,
        profile_etag=profile_etag,
        droid=droid,
        actor=actor,
        key=key,
        timeout=remaining,
    )


def _review_candidate(
    ctx: Context,
    *,
    store: Path,
    slug: str,
    rel: str,
    item: str,
    implementation_run: str,
    candidate: str,
    run: dict[str, Any],
    profile_name: str,
    profile: Profile,
    profile_etag: str,
    droid: DroidProfile,
    actor: str,
    key: str,
    timeout: float,
) -> dict[str, Any]:
    """Launch one separated reviewer at exactly ``candidate`` and keep its verdict.

    The candidate is passed explicitly because a merge reviews the commit its
    own rebase produced, which is not always the commit the run recorded.
    """
    root, _git_dir = checkout(ctx, action="a dispatch")
    review_id = new_ulid()
    paths = local_dispatch.review_paths(store, slug, review_id)
    started_at = now_iso()
    flight_started = time.monotonic()
    with local_dispatch.acquire_dispatch_lock(store, slug, review_id):
        _refuse_pending(store, slug)
        local_dispatch.write_review_binding(
            store,
            key,
            item=item,
            slug=slug,
            run=implementation_run,
            profile=profile_name,
            review_id=review_id,
        )
        local_dispatch.write_review_progress(
            store,
            review_id,
            item=slug,
            run=implementation_run,
            profile=profile_name,
            key_hash=sha256_hex(key.encode("utf-8")),
            candidate_commit=candidate,
            state="started",
            phase="started",
        )
        try:
            _create_worktree(root, paths, candidate)
            local_dispatch.register_review_worktree(
                store, review_id, paths.worktree, paths.branch
            )
        except BaseException:
            _review_rollback(store, key, review_id)
            raise

        role_file = write_role_file(
            store, review_id, profile=droid, body=profile.body
        )
        order_file = write_order_file(
            store,
            review_id,
            metadata=_review_metadata(
                ctx,
                slug=slug,
                rel=rel,
                candidate=candidate,
                profile_name=profile_name,
                profile=profile,
                profile_etag=profile_etag,
                paths=paths,
            ),
            sections=_review_sections(
                ctx, slug=slug, rel=rel, run=run, candidate=candidate, profile=profile
            ),
        )
        local_dispatch.write_review_progress(
            store, review_id, state="running", phase="running"
        )
        ran = run_droid(
            context=ctx,
            profile=droid,
            worktree=paths.worktree,
            confine_to=paths.worktree,
            role_file=role_file,
            order_file=order_file,
            timeout=timeout,
        )

    duration = round(time.monotonic() - flight_started, 3)
    outcome = _review_outcome(ran, candidate)
    report_id = _review_report(
        ctx, slug=slug, ran=ran, outcome=outcome
    )
    review_record_id: str | None = None
    check_verdict: dict[str, Any] | None = None
    if outcome["verdict"] is not None:
        review_record_id = _review_verdict_record(
            ctx,
            slug=slug,
            implementation_run=implementation_run,
            candidate=candidate,
            run=run,
            profile_name=profile_name,
            profile=profile,
            outcome=outcome,
        )
        check_verdict = _verify_review_check(ctx, slug, review_id, actor)
    verdict_ids = _verdict_ids(ctx, slug)
    accepted = bool(
        outcome["verdict"] == "approve"
        and check_verdict is not None
        and check_verdict.get("passed") is True
        and _candidate_checks_pass(ctx, slug, candidate)
    )
    ended_at = now_iso()
    review_run_id = _review_run_record(
        ctx,
        slug=slug,
        review_id=review_id,
        profile_name=profile_name,
        profile=profile,
        profile_etag=profile_etag,
        droid_model=profile.model,
        candidate=candidate,
        report_id=report_id,
        ran=ran,
        verdicts=verdict_ids,
        verified=outcome["verdict"] is not None,
        started_at=started_at,
        ended_at=ended_at,
        duration=duration,
    )
    evaluated = _evaluation_record(
        ctx,
        slug=slug,
        implementation_run=implementation_run,
        candidate=candidate,
        accepted=accepted,
        check_verdicts=verdict_ids,
        review=review_record_id,
        actor=actor,
    )
    status = (
        "invalid"
        if outcome["verdict"] is None
        else ("approved" if outcome["verdict"] == "approve" else "rejected")
    )
    value = {
        "run": review_run_id,
        "review": review_record_id,
        "commit": candidate,
        "verdict": outcome["verdict"],
        "findings": list(outcome["findings"]),
        "status": status,
    }
    local_dispatch.write_review_progress(
        store,
        review_id,
        state="completed",
        phase="completed",
        run=review_run_id,
        review=review_record_id,
        report=report_id,
        verdict=outcome["verdict"],
        status=status,
        evaluated=evaluated,
        result=value,
    )
    return value


def _review_replayed(
    store: Path, key: str, item: str, run: str, profile: str
) -> dict[str, Any] | None:
    """The stored result one review key already earned, or a refusal."""
    binding = local_dispatch.read_review_binding(store, key)
    if binding is None:
        return None
    if binding.get("corrupt"):
        raise WriteRefused(
            "CONFLICT",
            "the review key binding is unreadable; inspect it before retrying",
            {"key": key},
        )
    same = (binding.get("item"), binding.get("run"), binding.get("profile"))
    if same != (item, run, profile):
        raise WriteRefused(
            "CONFLICT",
            "this key already names a different review payload",
            {
                "key": key,
                "item": binding.get("item"),
                "run": binding.get("run"),
                "profile": binding.get("profile"),
                "review_id": binding.get("review_id"),
            },
        )
    review_id = str(binding.get("review_id"))
    progress = local_dispatch.read_review_progress(store, review_id) or {}
    if progress.get("state") == "completed" and isinstance(progress.get("result"), dict):
        return progress["result"]
    raise WriteRefused(
        "CONFLICT",
        "a review with this key has not completed; inspect the pending review",
        {
            "key": key,
            "review_id": review_id,
            "phase": progress.get("phase", "unknown"),
            "worktree": progress.get("worktree"),
            "branch": progress.get("branch"),
        },
    )


def _separation_reason(
    policy: Any,
    *,
    implementer: str,
    implementer_model: str,
    reviewer: str,
    reviewer_model: str,
    invoking: str,
) -> WriteRefused | None:
    """The refusal a review earns for not being separated, or None.

    The compared pair is the implementer run's actor against the reviewer
    profile; an invoker that is the implementer is refused the same way, since
    it would be reviewing its own candidate.
    """
    reason = review_separation(
        implementer,
        reviewer,
        implementer_model,
        reviewer_model,
        policy.reviewer_different_model,
    )
    if reason is None and invoking != reviewer:
        reason = review_separation(
            implementer,
            invoking,
            implementer_model,
            reviewer_model,
            policy.reviewer_different_model,
        )
    if reason is None:
        return None
    code = "PERMISSION_DENIED" if "model" not in reason else "VALIDATION_FAILED"
    return WriteRefused(
        code,
        reason,
        {
            "implementer_actor": implementer,
            "reviewer_actor": reviewer,
            "implementer_model": implementer_model,
            "reviewer_model": reviewer_model,
        },
    )


def _review_rollback(store: Path, key: str, review_id: str) -> None:
    """Forget a review that never reached the droid."""
    local_dispatch.remove_review_binding(store, key)
    local_dispatch.review_progress_path(store, review_id).unlink(missing_ok=True)
