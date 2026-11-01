"""One implementer continuation that may resolve only the paths a rebase left
conflicted, and the escalation the merge raises when it cannot."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.core.util import now_iso
from context_rail.gateway.handlers.common import bounded, profile_document, read_doc, skill_pins
from context_rail.model.cto import Profile, aggregate_usage, parse_report, parse_run
from context_rail.model.errors import ModelError
from context_rail.runner import git as git_runner
from context_rail.runner.droid import (
    droid_profile,
    extract_block,
    run_droid,
    write_order_file,
    write_role_file,
)
from context_rail.store import query
from context_rail.store.doc import Doc, etag
from context_rail.store.write import commit

from .budget import _effective_budget, _move_blocked, _run_facts
from .common import IMPL_ROLE, SUMMARY_MAX, _capture_payload, effective_seconds

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _continue_conflict(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    run: dict[str, Any],
    worktree: Path,
    onto: str,
    conflicts: tuple[str, ...],
    store: Path,
    merge_id: str,
    actor: str,
) -> dict[str, Any]:
    """One implementer session continuation that may resolve only the conflict."""
    profile_name = str(run["profile"] or IMPL_ROLE)
    _profile_rel, profile_etag, profile = profile_document(ctx, profile_name)
    droid = droid_profile(profile, name=profile_name)
    budget = _effective_budget(
        ctx.policy.budgets.get("item") or {} if ctx.policy else {},
        profile.budgets,
        ctx.pack.policy["retries"],
    )
    _facts, used_seconds, _attempts = _run_facts(ctx.index, slug)
    remaining = effective_seconds(budget) - used_seconds
    started_at = now_iso()
    clock = time.monotonic()
    if remaining <= 0:
        return {
            "resolved": False,
            "reason": "the Item has no time left for a conflict continuation",
            "run": None,
            "session_id": None,
            "duration_s": 0.0,
        }
    _data, doc = read_doc(store, rel)
    role_file = write_role_file(store, merge_id, profile=droid, body=profile.body)
    order_file = write_order_file(
        store,
        merge_id,
        metadata=_conflict_metadata(
            ctx,
            meta_id=merge_id,
            slug=slug,
            rel=rel,
            run=run,
            onto=onto,
            profile_name=profile_name,
            profile=profile,
            profile_etag=profile_etag,
            worktree=worktree,
        ),
        sections=_conflict_sections(
            ctx, doc=doc, slug=slug, onto=onto, conflicts=conflicts, profile=profile
        ),
    )
    ran = run_droid(
        context=ctx,
        profile=droid,
        worktree=worktree,
        confine_to=worktree,
        role_file=role_file,
        order_file=order_file,
        timeout=remaining,
        session_id=str(run["data"].get("session_id") or "") or None,
    )
    duration = round(time.monotonic() - clock, 3)
    still_running = git_runner.rebase_in_progress(worktree, worktree)
    left = git_runner.unmerged_paths(worktree, worktree)
    resolved = ran.failure is None and not still_running and not left
    head: str | None = None
    if resolved:
        head = git_runner.rev_parse_head(worktree, worktree)
    reason = None
    if not resolved:
        reason = (
            ran.failure
            or (
                "the rebase is still conflicted in " + ", ".join(left)
                if left
                else "the rebase did not finish after the continuation"
            )
        )
    report_id = _conflict_report(
        ctx, slug=slug, ran=ran, reason=reason, conflicts=conflicts, profile_name=profile_name
    )
    attempt = {
        "number": 1,
        "session_id": ran.session_id or run["data"].get("session_id"),
        "start": started_at,
        "end": now_iso(),
        "duration_s": duration,
        "exit": ran.exit,
        "timed_out": bool(ran.timed_out),
        "usage": dict(ran.usage),
        "runner_digest": ran.runner.get("digest"),
        "verdicts": [],
        "status": "verified" if resolved else "failed",
    }
    run_data = {
        "schema": 1,
        "dispatch_id": str(run["dispatch_id"]),
        "role": IMPL_ROLE,
        "actor": profile_name,
        "profile": profile_name,
        "profile_version": profile.version,
        "profile_etag": profile_etag,
        "skills": skill_pins(ctx, profile),
        "model": profile.model,
        "item": slug,
        "session_id": attempt["session_id"],
        "started_at": started_at,
        "ended_at": attempt["end"],
        "duration_s": duration,
        "exit": ran.exit,
        "status": "verified" if resolved else "failed",
        "attempts": [attempt],
        "candidate_commit": head,
        "accepted": None,
        "verdicts": [],
        "files_changed": list(conflicts),
        "commits_changed": [],
        "capture": _capture_payload(
            {"complete": True, "reason": None, "omitted": 0},
            int(ran.runner.get("omitted_bytes") or 0),
        ),
        "usage": aggregate_usage([attempt]),
        "runner_digest": ran.runner.get("digest"),
        "report": report_id,
    }
    parse_run(run_data)
    record = commit(
        store,
        ctx.manifest,
        [],
        {"kind": "run.completed", "about": [slug], "data": run_data},
        actor=actor,
        index=ctx.index,
    )
    return {
        "resolved": resolved,
        "reason": reason,
        "run": record.get("record"),
        "session_id": attempt["session_id"],
        "session_continued": bool(run["data"].get("session_id")),
        "duration_s": duration,
        "conflicts": list(left),
    }


def _conflict_metadata(
    ctx: Context,
    *,
    meta_id: str,
    slug: str,
    rel: str,
    run: dict[str, Any],
    onto: str,
    profile_name: str,
    profile: Profile,
    profile_etag: str,
    worktree: Path,
) -> dict[str, Any]:
    data, _doc = read_doc(ctx.store, rel)
    return {
        "schema": 1,
        "dispatch_id": meta_id,
        "item": slug,
        "item_etag": etag(data),
        "actor": profile_name,
        "profile": profile_name,
        "profile_version": profile.version,
        "profile_etag": profile_etag,
        "skills": skill_pins(ctx, profile),
        "base_commit": onto,
        "candidate_commit": str(run["candidate_commit"] or "") or None,
        "checks": [row["slug"] for row in query.check_states(ctx.index, slug)],
        "store": str(Path(ctx.store).absolute()),
        "worktree": str(worktree.absolute()),
    }


def _conflict_sections(
    ctx: Context,
    *,
    doc: Doc,
    slug: str,
    onto: str,
    conflicts: tuple[str, ...],
    profile: Profile,
) -> dict[str, str]:
    names = ", ".join(conflicts)
    checks = ", ".join(row["slug"] for row in query.check_states(ctx.index, slug))
    return {
        "objective": bounded(
            f"Resolve the rebase conflict in {names} against {onto} for {doc.title or slug}.",
            6000,
        ),
        "selected scope": ", ".join(doc.relations.get("part_of", ())) or "(none)",
        "conventions": "Keep the candidate's intent and the human commit's intent.",
        "constraints": (
            "Resolve only these conflicting paths: "
            + names
            + ". Change nothing else, run no command outside the worktree, and "
            "finish the rebase before ending your turn."
        ),
        "selected files": names,
        "checks": checks or "(none declared)",
        "allowed rail methods": ", ".join(profile.allowed_methods),
        "no-store-edit rule": (
            "Never write the canonical store; the parent records this continuation."
        ),
        "commit expectation": (
            "Complete the in-progress rebase: stage the resolved files and continue "
            "it. Do not merge, push or switch branches."
        ),
        "final report protocol": (
            "End your text with exactly one fenced ```rail-report block holding one "
            "JSON object with schema, target_class, target, item, run, "
            "nothing_to_report, what_happened, expected, observed, impact, evidence "
            "and fingerprint."
        ),
    }


def _conflict_report(
    ctx: Context,
    *,
    slug: str,
    ran: Any,
    reason: str | None,
    conflicts: tuple[str, ...],
    profile_name: str,
) -> str | None:
    block = extract_block(ran.text, "rail-report")
    data: dict[str, Any] | None = None
    if block is not None:
        try:
            parse_report(block)
            data = block
        except ModelError:
            data = None
    if data is None:
        data = {
            "schema": 1,
            "target_class": "environment",
            "target": slug,
            "item": slug,
            "run": None,
            "nothing_to_report": False,
            "what_happened": bounded(
                f"the conflict continuation for {slug!r} did not finish", SUMMARY_MAX
            ),
            "expected": "one continuation resolves the rebase conflict",
            "observed": bounded(
                str(reason or "the continuation printed no valid rail-report"), SUMMARY_MAX
            ),
            "impact": "medium",
            "evidence": list(conflicts)[:20],
            "fingerprint": None,
        }
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "report.filed", "about": [slug], "data": data},
        actor=profile_name,
        index=ctx.index,
    )
    return result.get("record")


def _merge_escalated(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    run: str,
    merge_id: str,
    candidate: str,
    branch: str,
    worktree: Path,
    rebased: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Escalate one failed conflict continuation and keep the state to inspect."""
    continuation = rebased["continuation"]
    data = {
        "schema": 1,
        "item": slug,
        "run": run,
        "merge_id": merge_id,
        "candidate_commit": candidate,
        "branch": branch,
        "worktree": str(worktree),
        "conflict_files": list(rebased["conflicts"]),
        "continuation_run": continuation.get("run"),
        "continuation_session": continuation.get("session_id"),
        "session_continued": continuation.get("session_continued"),
        "reason": str(rebased["escalation"]["reason"]),
        "attempts": 1,
        "evidence": [value for value in (continuation.get("run"),) if value][:20],
    }
    record = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "merge.escalated", "about": [slug], "data": data},
        actor=actor,
        index=ctx.index,
    )
    _move_blocked(ctx, slug, actor)
    return {
        "commit": None,
        "branch": branch,
        "merged": False,
        "pushed": False,
        "closed": None,
        "record": None,
        "escalation": {
            "record": record.get("record"),
            "reason": data["reason"],
            "phase": "rebase",
            "item": slug,
            "run": run,
            "candidate_commit": candidate,
            "worktree": str(worktree),
            "branch": branch,
            "conflict_files": list(rebased["conflicts"]),
            "continuation_run": continuation.get("run"),
        },
    }

