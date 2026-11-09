"""The Records a dispatch, merge or escalation owes, and the payload builders that
bound what the worker reported."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from context_rail.gateway.handlers.common import bounded
from context_rail.kernel.triage import failure_fingerprint
from context_rail.model.cto import Profile
from context_rail.runner import dispatch as local_dispatch
from context_rail.store.write import commit

from .common import SUMMARY_MAX, _Plan

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _started_record(
    ctx: Context,
    *,
    actor: str,
    dispatch_id: str,
    slug: str,
    expect: str,
    profile_name: str,
    profile: Profile,
    profile_etag: str,
    base: str,
    paths: local_dispatch.DispatchPaths,
) -> str | None:
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {
            "kind": "dispatch.started",
            "about": [slug],
            "data": {
                "schema": 1,
                "dispatch_id": dispatch_id,
                "item": slug,
                "item_etag": expect,
                "actor": actor,
                "profile": profile_name,
                "profile_version": profile.version,
                "profile_etag": profile_etag,
                "base_commit": base,
                "branch": paths.branch,
                "worktree": str(paths.worktree),
            },
        },
        actor=actor,
        index=ctx.index,
    )
    return result.get("record")


def _failure_report(
    plan: _Plan, summary: str, verdicts: list[str]
) -> dict[str, Any]:
    observed = summary or "the dispatch failed without a parsed result"
    return {
        "schema": 1,
        "target_class": "environment",
        "target": plan.slug,
        "item": plan.slug,
        "run": None,
        "nothing_to_report": False,
        "what_happened": bounded(
            f"the dispatch for {plan.slug!r} did not verify", SUMMARY_MAX
        ),
        "expected": "the declared Checks pass on the candidate commit",
        "observed": bounded(observed, SUMMARY_MAX),
        "impact": "medium",
        "evidence": verdicts[:20],
        "fingerprint": failure_fingerprint(observed),
    }


def _escalation_record(
    plan: _Plan,
    *,
    run_id: str | None,
    report_id: str | None,
    reasons: str,
    attempts: list[dict[str, Any]],
    candidate: str | None,
    verdicts: list[str],
) -> dict[str, Any]:
    evidence = [item for item in [report_id, *verdicts] if item][:20]
    data = {
        "schema": 1,
        "dispatch_id": plan.dispatch_id,
        "item": plan.slug,
        "run": run_id,
        "report": report_id,
        "reason": reasons or "the dispatch did not verify",
        "attempts": len(attempts),
        "worktree": str(plan.paths.worktree),
        "branch": plan.paths.branch,
        "candidate_commit": candidate,
        "verdicts": verdicts,
        "evidence": evidence,
    }
    result = commit(
        plan.store,
        plan.ctx.manifest,
        [],
        {"kind": "dispatch.escalated", "about": [plan.slug], "data": data},
        actor=plan.actor,
        index=plan.ctx.index,
    )
    return {
        "record": result.get("record"),
        "reason": data["reason"],
        "attempts": len(attempts),
        "run": run_id,
        "worktree": data["worktree"],
        "branch": data["branch"],
    }
