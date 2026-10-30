"""The Records one review leaves: its report, its immutable verdict bound to the
candidate, the review Check that carries it into the gate, and its run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.core.util import now_iso
from context_rail.gateway.handlers import checks as checks_handler
from context_rail.gateway.handlers.common import bounded, skill_pins
from context_rail.model.cto import Profile, aggregate_usage, parse_report, parse_run
from context_rail.model.errors import ModelError
from context_rail.runner.droid import extract_block
from context_rail.store import query
from context_rail.store.location import local_dir
from context_rail.store.write import ABSENT, WriteRefused, commit

from .common import REVIEW_ROLE, SUMMARY_MAX, _capture_payload

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _review_report(
    ctx: Context,
    *,
    slug: str,
    ran: Any,
    outcome: dict[str, Any],
) -> str | None:
    block = extract_block(ran.text, "rail-report")
    data: dict[str, Any] | None = None
    if block is not None and outcome["verdict"] is not None:
        try:
            parse_report(block)
            data = block
        except ModelError:
            data = None
    if data is None:
        reason = outcome.get("reason") or "the review printed no valid rail-report block"
        data = {
            "schema": 1,
            "target_class": "environment",
            "target": slug,
            "item": slug,
            "run": None,
            "nothing_to_report": False,
            "what_happened": bounded(
                f"the review of {slug!r} did not finish", SUMMARY_MAX
            ),
            "expected": "one separated verdict bound to the candidate commit",
            "observed": bounded(str(reason), SUMMARY_MAX),
            "impact": "medium",
            "evidence": list(outcome.get("evidence") or [])[:20],
            "fingerprint": None,
        }
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "report.filed", "about": [slug], "data": data},
        actor=REVIEW_ROLE,
        index=ctx.index,
    )
    return result.get("record")


def _review_verdict_record(
    ctx: Context,
    *,
    slug: str,
    implementation_run: str,
    candidate: str,
    run: dict[str, Any],
    profile_name: str,
    profile: Profile,
    outcome: dict[str, Any],
) -> str | None:
    data = {
        "schema": 1,
        "item": slug,
        "implementation_run": implementation_run,
        "implementer_actor": str(run["actor"] or ""),
        "reviewer_actor": profile_name,
        "implementer_model": str(run["model"] or ""),
        "reviewer_model": profile.model,
        "candidate_commit": candidate,
        "verdict": outcome["verdict"],
        "findings": list(outcome["findings"]),
        "evidence": list(outcome["evidence"]),
        "profile_version": profile.version,
    }
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "review.verdict", "about": [slug], "data": data},
        actor=profile_name,
        index=ctx.index,
    )
    return result.get("record")


def _verify_review_check(
    ctx: Context, slug: str, review_id: str, actor: str
) -> dict[str, Any] | None:
    """Verify the Item's review-mode Check against the verdict just written."""
    name = _review_check_slug(ctx, slug, review_id, actor)
    try:
        verified = checks_handler.verify(
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
        return None
    rows = [row for row in verified.get("verdicts", []) if row.get("check") == name]
    return rows[0] if rows else None


def _review_check_slug(ctx: Context, slug: str, review_id: str, actor: str) -> str:
    """The Item's review-mode Check slug, declaring one when it has none.

    A review Check is what binds the reviewer's verdict into the gate's proof;
    declaring it here keeps the associated verdict derivable from the review
    alone, and never edits an existing Check.
    """
    found = [
        row
        for row in query.check_states(ctx.index, slug)
        if row["mode"] == "review"
    ]
    if found:
        return str(found[0]["slug"])
    for name in (f"review-{slug}", *[f"review-{slug}-{n}" for n in range(2, 10)]):
        if _slug_in_use(ctx.index, name):
            continue
        spec = {
            "schema": 1,
            "mode": "review",
            "acceptance": False,
            "verifies": [slug],
        }
        spec_path = local_dir(Path(ctx.store)) / "review" / review_id / "check.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(
            json.dumps(spec), encoding="utf-8", newline="\n"
        )
        checks_handler.check(
            ctx,
            {
                "slug": name,
                "target": slug,
                "spec_file": str(spec_path),
                "expect": ABSENT,
                "key": None,
            },
            actor,
        )
        return name
    raise WriteRefused(
        "CONFLICT",
        f"{slug!r} already uses every review Check slug this tool would declare",
        {"item": slug},
    )


def _slug_in_use(ix: Any, name: str) -> bool:
    """Whether any document already answers to ``name``, not only a Check.

    Slugs are unique store-wide, so a Note that happens to carry the review
    Check's slug would make the declared Check a ``duplicate_slug`` error.
    """
    found = ix.conn.execute("SELECT 1 FROM docs WHERE slug = ? LIMIT 1", (name,)).fetchone()
    return found is not None


def _verdict_ids(ctx: Context, slug: str) -> list[str]:
    """The newest verdict Record id of every Check the Item declares."""
    return [
        str(row["verdict"])
        for row in query.check_states(ctx.index, slug)
        if row["verdict"] is not None
    ]


def _candidate_checks_pass(ctx: Context, slug: str, candidate: str) -> bool:
    """Whether every deterministic Check holds a fresh passing proof.

    The proof is the verdict's own binding: it passed, it was made from the
    Check's current definition, and any candidate it names is this one. Review
    Checks are excluded; their proof is the review verdict itself.
    """
    considered = 0
    for row in query.check_states(ctx.index, slug):
        if row["mode"] == "review":
            continue
        considered += 1
        if row["verdict"] is None or row["passed"] is not True:
            return False
        if row["check_etag"] != row["definition_etag"]:
            return False
        if row["mode"] == "cmd" and not row["input_blobs"]:
            return False
        if row["candidate_commit"] and row["candidate_commit"] != candidate:
            return False
    return considered > 0


def _review_run_record(
    ctx: Context,
    *,
    slug: str,
    review_id: str,
    profile_name: str,
    profile: Profile,
    profile_etag: str,
    droid_model: str,
    candidate: str,
    report_id: str | None,
    ran: Any,
    verdicts: list[str],
    verified: bool,
    started_at: str,
    ended_at: str,
    duration: float,
) -> str | None:
    attempt = {
        "number": 1,
        "session_id": ran.session_id,
        "start": started_at,
        "end": ended_at,
        "duration_s": duration,
        "exit": ran.exit,
        "timed_out": bool(ran.timed_out),
        "usage": dict(ran.usage),
        "runner_digest": ran.runner.get("digest"),
        "verdicts": verdicts,
        "status": "verified" if verified else "failed",
    }
    data = {
        "schema": 1,
        "dispatch_id": review_id,
        "role": REVIEW_ROLE,
        "actor": profile_name,
        "profile": profile_name,
        "profile_version": profile.version,
        "profile_etag": profile_etag,
        "skills": skill_pins(ctx, profile),
        "model": droid_model,
        "item": slug,
        "session_id": ran.session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": duration,
        "exit": ran.exit,
        "status": "verified" if verified else "failed",
        "attempts": [attempt],
        "candidate_commit": candidate,
        "accepted": None,
        "verdicts": verdicts,
        "files_changed": [],
        "commits_changed": [],
        "capture": _capture_payload(
            {"complete": True, "reason": None, "omitted": 0},
            int(ran.runner.get("omitted_bytes") or 0),
        ),
        "usage": aggregate_usage([attempt]),
        "runner_digest": ran.runner.get("digest"),
        "report": report_id,
    }
    parse_run(data)
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "run.completed", "about": [slug], "data": data},
        actor=profile_name,
        index=ctx.index,
    )
    return result.get("record")


def _evaluation_record(
    ctx: Context,
    *,
    slug: str,
    implementation_run: str,
    candidate: str,
    accepted: bool,
    check_verdicts: list[str],
    review: str | None,
    actor: str,
) -> str | None:
    data = {
        "schema": 1,
        "run": implementation_run,
        "candidate_commit": candidate,
        "accepted": bool(accepted),
        "check_verdicts": list(check_verdicts),
        "review": review,
        "at": now_iso(),
    }
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "run.evaluated", "about": [slug], "data": data},
        actor=actor,
        index=ctx.index,
    )
    return result.get("record")
