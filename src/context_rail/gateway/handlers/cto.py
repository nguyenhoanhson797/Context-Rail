"""The CTO's own reads and the single-instance tick session.

``cto brief`` renders the bounded context from the index. ``cto tick`` launches
exactly one CTO ``droid exec`` session in the canonical project checkout: it
holds ``local_dir/cto.lock`` for the whole child so a second tick is a
``CONFLICT``, refuses before the human approves the picture, builds the brief
and a bounded order, and records its own ``tick.started`` and ``run.completed``
Records with one report. Its own usage is recorded once on its own attempt
rows, so the ledger never mixes tick overhead with the worker runs the CTO
launches.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import new_ulid, now_iso, sha256_hex
from context_rail.gateway.handlers.common import (
    append_report,
    bounded,
    checkout as canonical_checkout,
    profile_document,
    required,
    skill_pins,
)
from context_rail.model.cto import (
    USAGE_KEYS,
    Profile,
    aggregate_usage,
    parse_report,
    parse_run,
)
from context_rail.model.errors import ModelError
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner import git as git_runner
from context_rail.runner.droid import (
    DroidProfile,
    droid_profile,
    extract_block,
    run_droid,
    write_order_file,
    write_role_file,
)
from context_rail.store import approval as store_approval
from context_rail.store.write import ABSENT, WriteRefused, commit

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["brief", "tick"]

CTO_ROLE = "cto"
TICK_METHOD = "cto tick"
DEFAULT_TICK_SECONDS = 7200.0
SUMMARY_MAX = 2000
BRIEF_CHARS = 12000
BRIEF_LIMIT = 20
PROMPT_ID_PREFIX = "tick-"
PROMPT_MAX_CHARS = 32000
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
CAPTURE_REASON = "the droid output exceeded the 262144-byte cap"

_ORDER_OBJECTIVE = (
    "You are the CTO of this project for one bounded session. Read the brief "
    "below, then run the loop through `rail` commands only:\n\n"
    "1. Triage the untriaged reports.\n"
    "2. Plan executable leaf Items and link them to the approved scope.\n"
    "3. Dispatch each executable Item to the implementer, then verify, review "
    "and merge it in that order.\n"
    "4. Escalate anything a gate blocks instead of working around it.\n"
    "5. Finish with the digest so the next session starts from observed state.\n\n"
    "You may not author or loosen acceptance Checks, change the goal or the "
    "scope, raise a budget or edit the store by hand. Those are human-only "
    "decisions; propose them and stop. There is no automatic scheduler: this "
    "session runs once and ends."
)

_ORDER_METHODS = (
    "Only these methods are yours: {methods}. Every change goes through one of "
    "them; the parent process writes the verdicts and Records."
)

_ORDER_SCOPE = (
    "The approved goal, the must/should/won't scope and every planned Item are "
    "in the brief. Work only inside that scope; a new deliverable is a proposal, "
    "not a silent addition."
)

_ORDER_CONVENTIONS = (
    "Follow the project's charter values and the repository's own conventions. "
    "Sub-agents write the failing test first, watch it fail for the right "
    "reason, then make it pass."
)

_ORDER_CONSTRAINTS = (
    "One session, one child, at most {seconds} seconds. You may not widen your "
    "own tools, autonomy, budget or methods, and you may not start a nested "
    "tick. A gate that blocks is an escalation, never a reason to loosen it."
)

_ORDER_FILES = (
    "Read only the files the brief and the dispatch work orders name. Never "
    "read or write outside the project checkout."
)

_ORDER_CHECKS = (
    "Decide every Item on observed proof: its declared Checks pass against "
    "committed inputs and a separated reviewer approves that exact candidate "
    "commit. Exit code 0 is an observation, not a verdict."
)

_ORDER_COMMITS = (
    "Sub-agents leave their work as commits on their own branch; you never "
    "merge, rebase or push by hand. `rail merge` is the only path onto the "
    "default branch."
)

_ORDER_STORE = (
    "The canonical store is {store}. Always pass it explicitly as "
    "`rail --store {store} <command>`. Never write it by hand and never adopt a "
    "store copied into another checkout: the parent writes every Record from "
    "your result."
)

_ORDER_REPORT = (
    "End your text with exactly one fenced ```rail-report block holding one JSON "
    "object with schema, target_class, target, item, run, nothing_to_report, "
    "what_happened, expected, observed, impact, evidence and fingerprint. Use "
    "nothing_to_report: true and empty narrative fields when there is nothing to "
    "report; a report is always filed. Prose never stands in for a Check."
)


def brief(context: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The bounded CTO brief, rendered from indexed projections only."""
    from context_rail.context import recipes

    return recipes.brief(
        context, max_chars=args["max_chars"], item_limit=args["item_limit"]
    )


# ── tick ──────────────────────────────────────────────────────────────────


def tick(context: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Run one CTO session behind the single-instance lock."""
    store = Path(context.store)
    key = required(args.get("key"), "a tick key", action="a tick")

    replayed = _replayed(store, key)
    if replayed is not None:
        return replayed

    state = store_approval.approval_state(context, "charter")
    if state["state"] != "approved":
        reason = "approval_missing" if state["state"] == "missing" else "approval_mismatch"
        raise WriteRefused(
            "INVALID_STATE",
            "the charter is not approved; nothing executes yet",
            {"reason": reason, "tool": TICK_METHOD},
        )

    _role_rel, profile_etag, profile = profile_document(context, CTO_ROLE)
    if profile.recipe != CTO_ROLE:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"profile {CTO_ROLE!r} is a {profile.recipe!r}, not a cto profile",
            {"profile": CTO_ROLE, "recipe": profile.recipe},
        )
    droid = droid_profile(profile, name=CTO_ROLE)
    timeout = _timeout(args.get("timeout"), profile, context)

    checkout, _git_dir = canonical_checkout(context, action="a tick")
    brief_value = brief(
        context, {"max_chars": BRIEF_CHARS, "item_limit": BRIEF_LIMIT}, actor
    )

    tick_id = new_ulid()
    with local_dispatch.acquire_tick_lock(store):
        local_dispatch.write_tick_binding(store, key, tick_id=tick_id, state="started")
        local_dispatch.write_tick_progress(
            store,
            tick_id,
            key=key,
            phase="started",
            started_at=now_iso(),
            timeout_s=timeout,
        )
        try:
            started = _started_record(
                context,
                actor=actor,
                tick_id=tick_id,
                key=key,
                profile=profile,
                profile_etag=profile_etag,
                checkout=checkout,
                timeout=timeout,
            )
            local_dispatch.write_tick_progress(
                store, tick_id, phase="prompts", started_record=started
            )
            role_file, order_file = _write_prompts(
                context,
                tick_id=tick_id,
                profile=profile,
                brief=brief_value,
                checkout=checkout,
            )
            local_dispatch.write_tick_progress(store, tick_id, phase="running")
            value = _execute(
                context,
                actor=actor,
                tick_id=tick_id,
                profile=profile,
                profile_etag=profile_etag,
                droid=droid,
                checkout=checkout,
                role_file=role_file,
                order_file=order_file,
                timeout=timeout,
            )
        except BaseException:
            local_dispatch.write_tick_progress(store, tick_id, phase="aborted")
            local_dispatch.remove_tick_binding(store, key)
            local_dispatch.tick_progress_path(store, tick_id).unlink(missing_ok=True)
            raise
        local_dispatch.write_tick_binding(
            store, key, tick_id=tick_id, state="completed", run=value["run"]
        )
        local_dispatch.write_tick_progress(
            store,
            tick_id,
            phase="completed",
            state="completed",
            status=value["status"],
            run=value["run"],
            report=(value["reports"] or [None])[0],
            result=value,
        )
    return value


def _timeout(raw: Any, profile: Profile, context: Context) -> float:
    """The bounded tick timeout: the effective cap unless a smaller one is asked."""
    cap = _effective_seconds(profile, context)
    if raw is None:
        return cap
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        raise WriteRefused(
            "VALIDATION_FAILED", "timeout must be a positive number of seconds"
        )
    if float(raw) > cap:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"timeout is limited to the CTO profile's {int(cap)} seconds",
            {"timeout": raw, "max_seconds": cap},
        )
    return float(raw)


def _effective_seconds(profile: Profile, context: Context) -> float:
    """The stricter non-null bound of the charter's project budget and the profile."""
    bounds: list[float] = []
    project: Any = None
    if context.policy is not None:
        project = (context.policy.budgets.get("project") or {}).get("seconds")
    for value in (profile.budgets.get("seconds"), project):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        bounds.append(float(value))
    return min(bounds) if bounds else DEFAULT_TICK_SECONDS


def _execute(
    context: Context,
    *,
    actor: str,
    tick_id: str,
    profile: Profile,
    profile_etag: str,
    droid: DroidProfile,
    checkout: Path,
    role_file: Path,
    order_file: Path,
    timeout: float,
) -> dict[str, Any]:
    started_at = now_iso()
    clock = time.monotonic()
    ran = run_droid(
        context=context,
        profile=droid,
        worktree=checkout,
        confine_to=checkout,
        role_file=role_file,
        order_file=order_file,
        timeout=timeout,
    )
    duration = round(time.monotonic() - clock, 3)
    ended_at = now_iso()
    status = STATUS_COMPLETED if ran.ok else STATUS_FAILED

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
        "verdicts": [],
        "status": status,
    }
    report_data = _report_payload(ran, status=status)
    report_id = append_report(context, report_data, actor=actor, about=[])

    run_data = {
        "schema": 1,
        "dispatch_id": tick_id,
        "role": CTO_ROLE,
        "actor": CTO_ROLE,
        "profile": CTO_ROLE,
        "profile_version": profile.version,
        "profile_etag": profile_etag,
        "skills": skill_pins(context, profile),
        "model": profile.model,
        "item": None,
        "session_id": ran.session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": duration,
        "exit": ran.exit,
        "status": status,
        "attempts": [attempt],
        "candidate_commit": None,
        "accepted": None,
        "verdicts": [],
        "files_changed": [],
        "commits_changed": [],
        "capture": _capture(ran),
        "usage": aggregate_usage([attempt]),
        "runner_digest": ran.runner.get("digest"),
        "report": report_id,
    }
    parse_run(run_data)
    result = commit(
        context.store,
        context.manifest,
        [],
        {"kind": "run.completed", "about": [], "data": run_data},
        actor=actor,
        index=context.index,
    )
    run_id = result.get("record")
    return {
        "run": run_id,
        "session_id": ran.session_id,
        "status": status,
        "reports": [report_id] if report_id else [],
        "usage": _visible_usage(run_data["usage"]),
    }


def _visible_usage(usage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The tick's counters: a known sum, and ``"unknown"`` where none was reported."""
    known = usage.get("known_sum") or {}
    unknown = usage.get("unknown_attempts") or {}
    return {
        name: ("unknown" if unknown.get(name) else known.get(name, "unknown"))
        for name in USAGE_KEYS
    }


def _capture(ran: Any) -> dict[str, Any]:
    omitted = int(ran.runner.get("omitted_bytes") or 0)
    return {
        "complete": omitted == 0,
        "reason": CAPTURE_REASON if omitted else None,
        "omitted": omitted,
    }


def _report_payload(ran: Any, *, status: str) -> dict[str, Any]:
    """The one report this tick owes: its own, or the observed failure."""
    if status == STATUS_COMPLETED and not ran.failure:
        block = extract_block(ran.text, "rail-report")
        if block is not None:
            try:
                parse_report(block)
                return block
            except ModelError:
                pass
    observed = ran.failure or "the CTO printed no valid rail-report block"
    return {
        "schema": 1,
        "target_class": "environment",
        "target": CTO_ROLE,
        "item": None,
        "run": None,
        "nothing_to_report": False,
        "what_happened": "the CTO tick did not finish cleanly",
        "expected": "one bounded CTO session ending with one rail-report block",
        "observed": bounded(str(observed), SUMMARY_MAX),
        "impact": "medium",
        "evidence": [],
        "fingerprint": None,
    }


def _started_record(
    context: Context,
    *,
    actor: str,
    tick_id: str,
    key: str,
    profile: Profile,
    profile_etag: str,
    checkout: Path,
    timeout: float,
) -> str | None:
    result = commit(
        context.store,
        context.manifest,
        [],
        {
            "kind": "tick.started",
            "about": [],
            "data": {
                "schema": 1,
                "dispatch_id": tick_id,
                "timeout_s": timeout,
                "actor": CTO_ROLE,
                "profile": CTO_ROLE,
                "profile_version": profile.version,
                "profile_etag": profile_etag,
                "model": profile.model,
                "autonomy": profile.auto,
                "store": str(Path(context.store).absolute()),
                "checkout": str(checkout),
                "key_hash": sha256_hex(key.encode("utf-8")),
            },
        },
        actor=actor,
        index=context.index,
    )
    return result.get("record")


def _write_prompts(
    context: Context,
    *,
    tick_id: str,
    profile: Profile,
    brief: dict[str, Any],
    checkout: Path,
) -> tuple[Path, Path]:
    """The role and order files, under ``local_dir/prompts/tick-<id>/``."""
    ident = f"{PROMPT_ID_PREFIX}{tick_id}"
    role_file = write_role_file(
        context.store,
        ident,
        profile=droid_profile(profile, name=CTO_ROLE),
        body=_role_text(profile),
    )
    snapshot = store_approval.approval_state(context, "charter")
    metadata = {
        "schema": 1,
        "dispatch_id": ident,
        "item": CTO_ROLE,
        "item_etag": snapshot.get("snapshot_digest") or ABSENT,
        "actor": CTO_ROLE,
        "profile": CTO_ROLE,
        "profile_version": profile.version,
        "profile_etag": snapshot.get("latest_record") or ident,
        "skills": skill_pins(context, profile),
        "base_commit": git_runner.rev_parse_head(checkout, checkout),
        "candidate_commit": None,
        "checks": [],
        "store": str(Path(context.store).absolute()),
        "worktree": str(checkout),
    }
    order_file = write_order_file(
        context.store,
        ident,
        metadata=metadata,
        sections=_order_sections(context, profile, brief),
    )
    return role_file, order_file


def _role_text(profile: Profile) -> str:
    """The CTO role prompt: the shipped body plus the tick's own hard limits."""
    body = profile.body.strip()
    extra = (
        "\n\nThis session is one tick. The single-instance lock is held for the "
        "whole session, so you cannot nest another tick; `cto tick` is not one "
        "of your methods. Stay inside the brief and the work order."
    )
    text = body + extra
    if len(text) > PROMPT_MAX_CHARS:
        text = text[: PROMPT_MAX_CHARS - 16] + "\n[truncated]"
    return text


def _order_sections(
    context: Context, profile: Profile, brief: dict[str, Any]
) -> dict[str, str]:
    store = str(Path(context.store).absolute())
    methods = ", ".join(
        method for method in profile.allowed_methods if method != TICK_METHOD
    )
    return {
        "objective": f"{_ORDER_OBJECTIVE}\n\n# The brief\n\n{brief['text']}",
        "selected scope": _ORDER_SCOPE,
        "conventions": _ORDER_CONVENTIONS,
        "constraints": _ORDER_CONSTRAINTS.format(
            seconds=int(_effective_seconds(profile, context))
        ),
        "selected files": _ORDER_FILES,
        "checks": _ORDER_CHECKS,
        "allowed rail methods": _ORDER_METHODS.format(methods=methods),
        "no-store-edit rule": _ORDER_STORE.format(store=store),
        "commit expectation": _ORDER_COMMITS,
        "final report protocol": _ORDER_REPORT,
    }


def _replayed(store: Path, key: str) -> dict[str, Any] | None:
    binding = local_dispatch.read_tick_binding(store, key)
    if binding is None:
        return None
    if binding.get("corrupt"):
        raise WriteRefused(
            "CONFLICT",
            "the tick key binding is unreadable; inspect it before retrying",
            {"key": key},
        )
    tick_id = str(binding.get("tick_id"))
    progress = local_dispatch.read_tick_progress(store, tick_id) or {}
    if progress.get("state") == "completed" and isinstance(progress.get("result"), dict):
        return progress["result"]
    raise WriteRefused(
        "CONFLICT",
        "a tick with this key has not completed; inspect the pending launch",
        {"key": key, "tick_id": tick_id, "phase": progress.get("phase", "unknown")},
    )
