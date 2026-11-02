"""Isolated implementer dispatches: one worktree, one branch, two attempts.

The canonical store is the caller's project checkout. A dispatch moves the Item
to its active category, checkpoints only store-owned work so the new worktree
inherits instructions, appends a durable ``dispatch.started`` Record, and only
then creates a private worktree and branch outside the repository. The worker's
prose is never proof: the attempt passes only when its candidate commit is
clean, free of store edits, and the declared Checks pass against committed
inputs. One failed attempt is retried in the same session; the second failure
blocks the Item and keeps the evidence."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.core.util import new_ulid, now_iso, sha256_hex
from context_rail.gateway.handlers.common import (
    append_report,
    bounded,
    checkout,
    profile_document,
    read_doc,
    required,
    skill_pins,
)
from context_rail.kernel.budgets import launch_refusal
from context_rail.model.cto import aggregate_usage, parse_report, parse_run
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, render_path
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner import git as git_runner
from context_rail.runner.droid import (
    droid_profile,
    extract_block,
    run_droid,
    write_order_file,
    write_role_file,
)
from context_rail.store import query
from context_rail.store.doc import Doc, etag, parse_doc
from context_rail.store.write import WriteRefused, commit, resolve_unique

from .budget import (
    _effective_budget,
    _move_active,
    _move_blocked,
    _require_executable,
    _run_facts,
)
from .checkpoint import (
    _checkpoint,
    _create_worktree,
    _current_branch,
    _status_entries,
    _store_paths,
)
from .common import (
    ADAPTER_PATHS,
    IMPL_ROLE,
    SUMMARY_MAX,
    _Plan,
    _capture_payload,
    _optional,
    _refuse_pending,
    _resolve_item,
    _verify_worktree,
    effective_attempts,
    effective_seconds,
)
from .records import _escalation_record, _failure_report, _started_record

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def dispatch(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create the isolated worktree, run the implementer, verify its candidate."""
    store = Path(ctx.store)
    item = required(args.get("item"), "an Item slug", action="dispatch")
    expect = required(args.get("expect"), "the Item's etag", action="dispatch")
    key = required(args.get("key"), "a dispatch key", action="dispatch")
    profile_name = _optional(args.get("profile")) or IMPL_ROLE

    replayed = _replayed(store, key, item, profile_name, expect)
    if replayed is not None:
        return replayed

    index = ctx.index
    slug, rel, current = _resolve_item(ctx, item)
    if current != expect:
        raise WriteRefused(
            "CONFLICT",
            f"{rel!r} is at etag {current}, not {expect}",
            {"path": rel, "expected": expect, "actual": current},
        )

    policy = ctx.policy
    if policy is None:
        raise WriteRefused(
            "INVALID_STATE", "the charter is not approved", {"reason": "approval_missing"}
        )
    _profile_rel, profile_etag, profile = profile_document(ctx, profile_name)
    if profile.recipe != IMPL_ROLE:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"profile {profile_name!r} is a {profile.recipe!r}, not an implementer",
            {"profile": profile_name, "recipe": profile.recipe},
        )
    droid = droid_profile(profile, name=profile_name)
    budget = _effective_budget(policy.budgets.get("item") or {}, profile.budgets, ctx.pack.policy["retries"])
    facts, used_seconds, used_attempts = _run_facts(index, slug)
    refusal = launch_refusal(facts, budget)
    if refusal is not None:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            f"{slug!r} has no dispatch budget left: {refusal}",
            {"item": slug, "reason": refusal},
        )
    _require_executable(ctx, slug)

    root, _git_dir = checkout(ctx, action="a dispatch")
    branch_now = _current_branch(root)
    if branch_now != policy.default_branch:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout is not on its approved default branch",
            {"current": branch_now, "expected": policy.default_branch},
        )

    dispatch_id = new_ulid()
    paths = local_dispatch.dispatch_paths(store, slug, dispatch_id)
    with local_dispatch.acquire_dispatch_lock(store, slug, dispatch_id):
        _refuse_pending(store, slug)
        local_dispatch.write_binding(
            store,
            key,
            item=item,
            slug=slug,
            profile=profile_name,
            expect=expect,
            dispatch_id=dispatch_id,
        )
        local_dispatch.write_progress(
            store,
            dispatch_id,
            item=slug,
            profile=profile_name,
            expect=expect,
            key_hash=sha256_hex(key.encode("utf-8")),
            state="started",
            phase="started",
            notes=[],
        )
        _data, original_doc = read_doc(store, rel)
        original_status = original_doc.status
        created = False
        try:
            _move_active(ctx, slug, actor)
            store_paths = _store_paths(ctx, root)
            base = _checkpoint(ctx, root, store_paths, actor)
            started = _started_record(
                ctx,
                actor=actor,
                dispatch_id=dispatch_id,
                slug=slug,
                expect=expect,
                profile_name=profile_name,
                profile=profile,
                profile_etag=profile_etag,
                base=base,
                paths=paths,
            )
            local_dispatch.write_progress(
                store,
                dispatch_id,
                base_commit=base,
                branch=paths.branch,
                started_record=started,
            )
            _create_worktree(root, paths, base)
            created = True
            local_dispatch.register_worktree(
                store, dispatch_id, paths.worktree, paths.branch
            )
        except BaseException:
            _rollback(store, key, dispatch_id)
            if not created:
                _restore_status(ctx, slug, original_status, actor)
            raise

        checks = tuple(row["slug"] for row in query.check_states(index, slug))
        plan = _Plan(
            ctx=ctx,
            store=store,
            actor=actor,
            slug=slug,
            expect=expect,
            profile_name=profile_name,
            profile=profile,
            profile_etag=profile_etag,
            droid=droid,
            budget=budget,
            dispatch_id=dispatch_id,
            paths=paths,
            base=base,
            store_paths=store_paths,
            checks=checks,
            skill_pins=skill_pins(ctx, profile),
            used_seconds=used_seconds,
            max_attempts=max(1, effective_attempts(budget) - used_attempts),
        )
        role_file, order_file = _write_prompts(ctx, plan, rel)
        return _execute(plan, role_file, order_file)


def _write_prompts(ctx: Context, plan: _Plan, rel: str) -> tuple[Path, Path]:
    role_file = write_role_file(
        plan.store, plan.dispatch_id, profile=plan.droid, body=plan.profile.body
    )
    _data, doc = read_doc(plan.store, rel)
    order_file = write_order_file(
        plan.store,
        plan.dispatch_id,
        metadata={
            "schema": 1,
            "dispatch_id": plan.dispatch_id,
            "item": plan.slug,
            "item_etag": plan.expect,
            "actor": plan.profile_name,
            "profile": plan.profile_name,
            "profile_version": plan.profile.version,
            "profile_etag": plan.profile_etag,
            "skills": plan.skill_pins,
            "base_commit": plan.base,
            "candidate_commit": None,
            "checks": list(plan.checks),
            "store": str(plan.store.absolute()),
            "worktree": str(plan.paths.worktree.absolute()),
        },
        sections=_sections(plan, doc),
    )
    return role_file, order_file


def _sections(plan: _Plan, doc: Doc) -> dict[str, str]:
    title = doc.title or plan.slug
    scope = ", ".join(doc.relations.get("part_of", ())) or "(none)"
    methods = ", ".join(plan.profile.allowed_methods)
    check_lines = "\n".join(f"- {slug}" for slug in plan.checks) or "(none declared)"
    return {
        "objective": bounded(f"{title}\n\n{doc.body}", 6000),
        "selected scope": scope,
        "conventions": (
            "Follow the repository's own conventions. Write the failing test first, "
            "watch it fail for the right reason, then make it pass."
        ),
        "constraints": (
            f"One session, at most {plan.max_attempts} attempt(s) and "
            f"{int(effective_seconds(plan.budget))} seconds. Never stage files to make "
            "a Check pass, and never widen your own tools, autonomy or budget."
        ),
        "selected files": ", ".join(
            sorted({glob for row in query.check_states(plan.ctx.index, plan.slug)
                    for glob in row["inputs"]})
        )
        or "(none declared)",
        "checks": check_lines,
        "allowed rail methods": methods,
        "no-store-edit rule": (
            "Write only `code` notes in this worktree's store: `rail add code ...`, "
            "`rail update ...` and `rail link ...`. Never use "
            f"`rail --store {plan.store.absolute()}` (the canonical store); the parent "
            "writes every other Record and performs the merge. Any other store path in "
            "your candidate is refused."
        ),
        "commit expectation": (
            "Leave your work as commits on the current branch. Do not merge, rebase, push "
            "or switch branches."
        ),
        "final report protocol": (
            "End your text with exactly one fenced ```rail-report block holding one JSON "
            "object with schema, target_class, target, item, run, nothing_to_report, "
            "what_happened, expected, observed, impact, evidence and fingerprint. Prose "
            "never stands in for a Check."
        ),
    }


def _execute(plan: _Plan, role_file: Path, order_file: Path) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    verdicts: list[str] = []
    last_report: dict[str, Any] | None = None
    failures: list[str] = []
    summary = ""
    output_omitted = 0
    capture: dict[str, Any] = {
        "files_changed": [],
        "commits_changed": [],
        "capture": {"complete": True, "reason": None, "omitted": 0},
    }
    session: str | None = None
    started_at = now_iso()
    flight_started = time.monotonic()
    deadline = max(1.0, effective_seconds(plan.budget) - plan.used_seconds)

    for number in range(1, plan.max_attempts + 1):
        elapsed = time.monotonic() - flight_started
        remaining = deadline - elapsed
        if remaining <= 0:
            break
        attempt_started = now_iso()
        clock = time.monotonic()
        retry_text = None
        if number > 1:
            retry_text = summary or f"attempt {number - 1} did not verify"
            if session is None:
                local_dispatch.write_progress(
                    plan.store,
                    plan.dispatch_id,
                    notes=[
                        f"attempt {number - 1} reported no session; attempt {number} "
                        "started a new session"
                    ],
                )
        local_dispatch.write_progress(
            plan.store,
            plan.dispatch_id,
            state="running",
            phase="running",
            attempts=len(attempts) + 1,
        )
        ran = run_droid(
            context=plan.ctx,
            profile=plan.droid,
            worktree=plan.paths.worktree,
            confine_to=plan.paths.worktree,
            role_file=role_file,
            order_file=order_file,
            timeout=min(remaining, deadline),
            session_id=session if number > 1 else None,
            failure_text=retry_text,
        )
        duration = round(time.monotonic() - clock, 3)
        if ran.session_id:
            session = ran.session_id
        local_dispatch.write_progress(
            plan.store,
            plan.dispatch_id,
            state="verifying",
            phase="verifying",
        )
        reasons, attempt_verdicts, block, changes = _judge(plan, ran)
        capture = changes
        verdicts = attempt_verdicts
        output_omitted = int(ran.runner.get("omitted_bytes") or 0)
        attempt = {
            "number": number,
            "session_id": ran.session_id or session,
            "start": attempt_started,
            "end": now_iso(),
            "duration_s": duration,
            "exit": ran.exit,
            "timed_out": bool(ran.timed_out),
            "usage": dict(ran.usage),
            "runner_digest": ran.runner.get("digest"),
            "verdicts": attempt_verdicts,
            "status": "verified" if not reasons else "failed",
        }
        attempts.append(attempt)
        if reasons:
            failures.append(f"attempt {number}: " + "; ".join(reasons))
            stderr = (ran.runner.get("stderr") or "").strip()
            if stderr:
                failures.append("stderr: " + bounded(stderr, 800))
            summary = bounded("\n".join(failures), SUMMARY_MAX)
        else:
            last_report = block
            summary = ""
            break

    verified = bool(attempts) and attempts[-1]["status"] == "verified"
    status = "verified" if verified else "failed"
    ended_at = now_iso()
    total_seconds = round(time.monotonic() - flight_started, 3)
    candidate = _candidate(plan)
    public_capture = _capture_payload(capture, output_omitted)
    report_data = last_report if verified else _failure_report(plan, summary, verdicts)
    report_id = append_report(plan.ctx, report_data, actor=plan.actor, about=[plan.slug])
    run_data = {
        "schema": 1,
        "dispatch_id": plan.dispatch_id,
        "role": plan.profile.recipe,
        "actor": plan.profile_name,
        "profile": plan.profile_name,
        "profile_version": plan.profile.version,
        "profile_etag": plan.profile_etag,
        "skills": plan.skill_pins,
        "model": plan.profile.model,
        "item": plan.slug,
        "session_id": session,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": total_seconds,
        "exit": attempts[-1]["exit"] if attempts else None,
        "status": status,
        "attempts": attempts,
        "candidate_commit": candidate,
        "accepted": None,
        "verdicts": verdicts,
        "files_changed": capture["files_changed"],
        "commits_changed": capture["commits_changed"],
        "capture": public_capture,
        "usage": aggregate_usage(attempts),
        "runner_digest": attempts[-1]["runner_digest"] if attempts else None,
        "report": report_id,
    }
    parse_run(run_data)
    run_record = commit(
        plan.store,
        plan.ctx.manifest,
        [],
        {"kind": "run.completed", "about": [plan.slug], "data": run_data},
        actor=plan.actor,
        index=plan.ctx.index,
    )
    run_id = run_record.get("record")

    escalation = None
    if not verified:
        _move_blocked(plan.ctx, plan.slug, plan.actor)
        escalation = _escalation_record(
            plan,
            run_id=run_id,
            report_id=report_id,
            reasons=summary,
            attempts=attempts,
            candidate=candidate,
            verdicts=verdicts,
        )

    value = {
        "dispatch_id": plan.dispatch_id,
        "run": run_id,
        "branch": plan.paths.branch,
        "worktree": str(plan.paths.worktree),
        "attempts": len(attempts),
        "verdicts": verdicts,
        "status": "verified" if verified else "escalated",
        "escalation": escalation,
    }
    local_dispatch.write_progress(
        plan.store,
        plan.dispatch_id,
        state="completed",
        phase="completed",
        run=run_id,
        report=report_id,
        verdicts=verdicts,
        status=value["status"],
        escalation=escalation,
        result=value,
    )
    return value


_CODE_NOTE_KINDS = frozenset({"file", "module", "class", "function", "method"})
_CODE_RECORD_KINDS = frozenset(
    {"doc.created", "doc.updated", "relation.changed", "decision.made"}
)


def _code_map_changes(
    manifest: Manifest,
    worktree: Path,
    store_paths: tuple[str, ...],
    changes: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Split a candidate's store changes into code-map files/records and the rest.

    A worker may carry only a valid ``code`` note and the records that write it.
    A truncated capture cannot prove what it left out, so every store change is
    refused in that case.
    """
    store_changed = [
        path for path in changes.get("store_changed", ()) if isinstance(path, str)
    ]
    if not changes.get("complete") or not store_changed:
        return [], store_changed
    store_roots = [prefix for prefix in store_paths if prefix not in ADAPTER_PATHS]
    store_prefix = "" if len(store_roots) > 1 else (store_roots[0] if store_roots else "")
    note_prefix = render_path(
        manifest, primitive="note", preset="code", slug="x"
    ).rsplit("/", 1)[0]
    record_roots = {
        render_path(
            manifest,
            primitive="record",
            preset="",
            slug="x",
            record_id="x",
            at=now_iso(),
        ).split("/", 1)[0],
        render_path(
            manifest,
            primitive="record",
            preset="event",
            slug="x",
            record_id="x",
            at=now_iso(),
        ).split("/", 1)[0],
        render_path(
            manifest,
            primitive="record",
            preset="decision",
            slug="x",
            record_id="x",
            at=now_iso(),
        ).split("/", 1)[0],
    }
    notes: dict[str, str] = {}
    allowed: set[str] = set()
    for path in store_changed:
        rel = _store_relative(path, store_prefix)
        if rel is None or not _under(rel, note_prefix) or not rel.endswith(".md"):
            continue
        doc = _read_worktree_doc(worktree / path, rel)
        if doc is None or doc.problems:
            continue
        if doc.primitive != "note" or doc.preset != "code":
            continue
        source = doc.fields.get("source_path")
        if not isinstance(source, str) or not source.strip():
            continue
        if doc.fields.get("kind") not in _CODE_NOTE_KINDS:
            continue
        notes[path] = doc.slug
        allowed.add(path)
    code_slugs = set(notes.values())
    recorded: set[str] = set()
    for path in store_changed:
        if path in allowed:
            continue
        rel = _store_relative(path, store_prefix)
        if rel is None or rel.split("/", 1)[0] not in record_roots:
            continue
        doc = _read_worktree_doc(worktree / path, rel)
        if doc is None or doc.problems or doc.primitive != "record":
            continue
        if doc.fields.get("kind") not in _CODE_RECORD_KINDS:
            continue
        about = code_slugs.intersection(doc.relations.get("about", ()))
        if not about:
            continue
        recorded.update(about)
        allowed.add(path)
    unrecorded = code_slugs - recorded
    if unrecorded:
        allowed = {
            path for path in allowed if notes.get(path) not in unrecorded
        }
    return (
        [path for path in store_changed if path in allowed],
        [path for path in store_changed if path not in allowed],
    )


def _store_relative(path: str, prefix: str) -> str | None:
    prefix = prefix.strip("/")
    if prefix in ("", "."):
        return path
    if path == prefix:
        return ""
    head = prefix + "/"
    return path[len(head) :] if path.startswith(head) else None


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.strip("/")
    return path == prefix or path.startswith(prefix + "/")


def _read_worktree_doc(path: Path, rel: str) -> Doc | None:
    try:
        return parse_doc(rel, path.read_bytes())
    except OSError:
        return None


def _judge(
    plan: _Plan, ran: Any
) -> tuple[list[str], list[str], dict[str, Any] | None, dict[str, Any]]:
    reasons: list[str] = []
    if ran.failure:
        reasons.append(ran.failure)
    changes = git_runner.capture_changes(
        worktree=plan.paths.worktree,
        confine_to=plan.paths.worktree,
        base_commit=plan.base,
        store_paths=plan.store_paths,
    )
    _allowed, refused = _code_map_changes(
        plan.ctx.manifest, plan.paths.worktree, plan.store_paths, changes
    )
    if refused:
        reasons.append(
            "the worker changed store-owned or adapter paths: " + ", ".join(refused)
        )
    dirty = _status_entries(plan.paths.worktree)
    if dirty:
        reasons.append("the candidate worktree is not clean: " + ", ".join(dirty[:20]))
    verdicts: list[str] = []
    block: dict[str, Any] | None = None
    if not reasons:
        head = git_runner.rev_parse_head(plan.paths.worktree, plan.paths.worktree)
        if head == plan.base:
            reasons.append("the worker left no candidate commit")
    if not reasons:
        outcome = _verify(plan)
        verdicts = [row["record"] for row in outcome["verdicts"] if row.get("record")]
        if "error" in outcome:
            reasons.append(outcome["error"])
        elif not outcome["passed"]:
            failing = [row for row in outcome["verdicts"] if not row["passed"]]
            detail = (
                "; ".join(f"{row['check']}: {row['reason']}" for row in failing)
                or "no executable Check was verified"
            )
            reasons.append("the declared Checks failed: " + detail)
    if not reasons:
        block = extract_block(ran.text, "rail-report")
        if block is None:
            reasons.append("the run printed no valid rail-report block")
        else:
            try:
                parse_report(block)
            except ModelError as error:
                reasons.append("the run's rail-report is invalid: " + error.message)
                block = None
    return reasons, verdicts, block, changes


def _verify(plan: _Plan) -> dict[str, Any]:
    return _verify_worktree(plan.ctx, plan.slug, plan.paths.worktree, plan.actor)


def _replayed(
    store: Path, key: str, item: str, profile: str, expect: str
) -> dict[str, Any] | None:
    """The stored result one key already earned, or a refusal for a mismatch."""
    binding = local_dispatch.read_binding(store, key)
    if binding is None:
        return None
    if binding.get("corrupt"):
        raise WriteRefused(
            "CONFLICT",
            "the dispatch key binding is unreadable; inspect it before retrying",
            {"key": key},
        )
    same = (binding.get("item"), binding.get("profile"), binding.get("expect"))
    if same != (item, profile, expect):
        raise WriteRefused(
            "CONFLICT",
            "this key already names a different dispatch payload",
            {
                "key": key,
                "item": binding.get("item"),
                "profile": binding.get("profile"),
                "expect": binding.get("expect"),
                "dispatch_id": binding.get("dispatch_id"),
            },
        )
    dispatch_id = str(binding.get("dispatch_id"))
    progress = local_dispatch.read_progress(store, dispatch_id) or {}
    if progress.get("state") == "completed" and isinstance(progress.get("result"), dict):
        return progress["result"]
    raise WriteRefused(
        "CONFLICT",
        "a dispatch with this key has not completed; inspect the pending launch",
        {
            "key": key,
            "dispatch_id": dispatch_id,
            "phase": progress.get("phase", "unknown"),
            "worktree": progress.get("worktree"),
            "branch": progress.get("branch"),
        },
    )


def _rollback(store: Path, key: str, dispatch_id: str) -> None:
    """Forget a launch that never reached the droid."""
    local_dispatch.remove_binding(store, key)
    local_dispatch.progress_path(store, dispatch_id).unlink(missing_ok=True)


def _restore_status(ctx: Context, slug: str, status: str | None, actor: str) -> None:
    """Undo the active move a launch made before it failed to create a worktree."""
    if not status:
        return
    from context_rail.gateway.handlers.docs import move as move_item

    try:
        rel = resolve_unique(ctx.index, slug)
        data, doc = read_doc(Path(ctx.store), rel)
        if doc.status == status:
            return
        move_item(
            ctx,
            {
                "slug": slug,
                "status": status,
                "expect": etag(data),
                "resolution": None,
                "confirm": None,
                "key": None,
            },
            actor,
        )
    except WriteRefused:
        return


def _candidate(plan: _Plan) -> str | None:
    try:
        head = git_runner.rev_parse_head(plan.paths.worktree, plan.paths.worktree)
    except WriteRefused:
        return None
    return None if head == plan.base else head

