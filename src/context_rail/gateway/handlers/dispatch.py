"""Isolated implementer dispatches: one worktree, one branch, two attempts.

The canonical store is the caller's project checkout. A dispatch moves the Item
to its active category, checkpoints only store-owned work so the new worktree
inherits instructions, appends a durable ``dispatch.started`` Record, and only
then creates a private worktree and branch outside the repository. The worker's
prose is never proof: the attempt passes only when its candidate commit is
clean, free of store edits, and the declared Checks pass against committed
inputs. One failed attempt is retried in the same session; the second failure
blocks the Item and keeps the evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from context_rail.core.util import new_ulid, now_iso, sha256_hex
from context_rail.gateway.handlers import checks as checks_handler
from context_rail.gateway.handlers.common import read_doc
from context_rail.gateway.handlers.docs import move as move_item
from context_rail.kernel import structure
from context_rail.kernel.budgets import (
    Budget,
    RunFacts,
    AttemptUsage,
    launch_refusal,
)
from context_rail.kernel.lifecycle import category_of
from context_rail.model.cto import (
    Profile,
    aggregate_usage,
    parse_profile,
    parse_report,
    parse_run,
)
from context_rail.model.errors import ModelError
from context_rail.model.manifest import render_path
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
from context_rail.store import query
from context_rail.store.doc import Doc, etag, parse_doc
from context_rail.store.location import PathEscape, confine, worktree_git_dir
from context_rail.store.write import WriteRefused, commit, resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["dispatch"]

IMPL_ROLE = "implementer"
BUILTIN_ATTEMPTS = 2
BUILTIN_SECONDS = 3600.0
CAPTURE_REASON = "the droid output exceeded the 262144-byte cap"
SUMMARY_MAX = 2000
ADAPTER_PATHS = ("AGENTS.md", ".agents")
CHECKOUT_MESSAGE = "chore: checkpoint the store before a dispatch"


@dataclass
class _Plan:
    """Everything one launch needs, assembled before the droid can start."""

    ctx: Context
    store: Path
    actor: str
    slug: str
    expect: str
    profile_name: str
    profile: Profile
    profile_etag: str
    droid: DroidProfile
    budget: Budget
    dispatch_id: str
    paths: local_dispatch.DispatchPaths
    base: str
    store_paths: tuple[str, ...]
    checks: tuple[str, ...]
    skill_pins: list[dict[str, Any]]
    used_seconds: float
    max_attempts: int


def dispatch(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create the isolated worktree, run the implementer, verify its candidate."""
    store = Path(ctx.store)
    item = _required(args.get("item"), "an Item slug")
    expect = _required(args.get("expect"), "the Item's etag")
    key = _required(args.get("key"), "a dispatch key")
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
    _profile_rel, profile_etag, profile = _load_profile(ctx, profile_name)
    if profile.recipe != IMPL_ROLE:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"profile {profile_name!r} is a {profile.recipe!r}, not an implementer",
            {"profile": profile_name, "recipe": profile.recipe},
        )
    droid = droid_profile(profile, name=profile_name)
    budget = _effective_budget(policy.budgets.get("item") or {}, profile.budgets)
    facts, used_seconds, used_attempts = _run_facts(index, slug)
    refusal = launch_refusal(facts, budget)
    if refusal is not None:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            f"{slug!r} has no dispatch budget left: {refusal}",
            {"item": slug, "reason": refusal},
        )
    _require_executable(ctx, slug)

    root, _git_dir = _checkout(ctx)
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
            _create_worktree(root, paths, policy.default_branch, base)
            local_dispatch.register_worktree(
                store, dispatch_id, paths.worktree, paths.branch
            )
        except BaseException:
            _rollback(store, key, dispatch_id)
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
            skill_pins=_skill_pins(ctx, profile),
            used_seconds=used_seconds,
            max_attempts=max(1, int(budget.attempts or BUILTIN_ATTEMPTS) - used_attempts),
        )
        role_file, order_file = _write_prompts(ctx, plan, rel)
        return _execute(plan, role_file, order_file)


# ── preflight ─────────────────────────────────────────────────────────────


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


def _refuse_pending(store: Path, slug: str) -> None:
    pending = local_dispatch.pending_dispatches(store, slug)
    if not pending:
        return
    entry = pending[0]
    raise WriteRefused(
        "CONFLICT",
        "a dispatch for this Item is already pending; inspect it before retrying",
        {
            "dispatch_id": entry.get("dispatch_id"),
            "phase": entry.get("phase", "unknown"),
            "worktree": entry.get("worktree"),
            "branch": entry.get("branch"),
        },
    )


def _resolve_item(ctx: Context, item: str) -> tuple[str, str, str]:
    rel = resolve_unique(ctx.index, item)
    data, doc = read_doc(ctx.store, rel)
    if doc.primitive != "item":
        raise WriteRefused(
            "VALIDATION_FAILED", f"{rel!r} is not an Item", {"path": rel}
        )
    return doc.slug, rel, etag(data)


def _load_profile(ctx: Context, name: str) -> tuple[str, str, Profile]:
    """The store's own copy of one Actor profile, its etag and its parse."""
    candidates: list[tuple[str, bytes, Doc]] = []
    for preset in ctx.pack.presets.values():
        if preset.primitive != "actor":
            continue
        try:
            rel = render_path(
                ctx.manifest, primitive="actor", preset=preset.name, slug=name
            )
        except ModelError:
            continue
        path = Path(ctx.store) / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        doc = parse_doc(rel, data)
        if doc.slug == name:
            candidates.append((rel, data, doc))
    if not candidates:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"unknown profile {name!r}; onboarding ships the implementer",
            {"profile": name},
        )
    if len(candidates) > 1:
        raise WriteRefused(
            "CONFLICT",
            f"{name!r} names more than one profile",
            {"paths": sorted(rel for rel, _data, _doc in candidates)},
        )
    rel, data, doc = candidates[0]
    try:
        profile = parse_profile(doc.fields, body=doc.body)
    except ModelError as error:
        raise WriteRefused(error.code, error.message) from None
    return rel, etag(data), profile


def _skill_pins(ctx: Context, profile: Profile) -> list[dict[str, Any]]:
    """Pin every selected skill's source path, version and etag.

    A profile may name a scoped copy; when the store ships only the shared
    source for the same slug, that source is the document the pin describes.
    """
    pins: list[dict[str, Any]] = []
    for declared in profile.skills:
        rel, data = _skill_source(ctx, declared)
        doc = parse_doc(rel, data)
        version = doc.fields.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"skill {rel!r} carries no positive version",
                {"skill": rel},
            )
        pins.append({"identity": rel, "version": version, "etag": etag(data)})
    return pins


def _skill_source(ctx: Context, declared: str) -> tuple[str, bytes]:
    found = _read_confined(ctx.store, declared)
    if found is not None:
        return declared, found
    stem = PurePosixPath(declared).stem
    try:
        shared = render_path(
            ctx.manifest, primitive="note", preset="skill", slug=stem
        )
    except ModelError:
        shared = None
    if shared is not None:
        found = _read_confined(ctx.store, shared)
        if found is not None:
            return shared, found
    raise WriteRefused(
        "VALIDATION_FAILED",
        f"the profile names a missing skill {declared!r}",
        {"skill": declared},
    )


def _read_confined(store: Path, rel: str) -> bytes | None:
    try:
        path = confine(store, rel)
    except PathEscape:
        return None
    return path.read_bytes() if path.is_file() else None


def _effective_budget(charter: dict[str, Any], profile: dict[str, Any]) -> Budget:
    """The stricter non-null bound of the charter and the profile."""
    return Budget(
        attempts=int(_stricter(charter.get("attempts"), profile.get("attempts"), BUILTIN_ATTEMPTS)),
        seconds=float(_stricter(charter.get("seconds"), profile.get("seconds"), BUILTIN_SECONDS)),
        tokens=_stricter(charter.get("tokens"), profile.get("tokens"), None),
        credits=_stricter(charter.get("credits"), profile.get("credits"), None),
    )


def _stricter(first: Any, second: Any, default: Any) -> Any:
    values = [value for value in (first, second) if value is not None]
    return min(values) if values else default


def _run_facts(index: Any, slug: str) -> tuple[list[RunFacts], float, int]:
    facts: list[RunFacts] = []
    used = 0.0
    used_attempts = 0
    for row in query.run_facts(index, slug):
        used += float(row["seconds"])
        used_attempts += int(row["attempts"])
        facts.append(
            RunFacts(
                item=slug,
                role=row["role"],
                attempts=row["attempts"],
                seconds=float(row["seconds"]),
                usage=tuple(
                    AttemptUsage(
                        input_tokens=usage.get("input_tokens", "unknown"),
                        output_tokens=usage.get("output_tokens", "unknown"),
                        factory_credits=usage.get("factory_credits", "unknown"),
                    )
                    for usage in row["usage"]
                ),
                run_id=row["run"],
            )
        )
    return facts, used, used_attempts


def _require_executable(ctx: Context, slug: str) -> None:
    if slug in structure.executable(_item_rows(ctx)):
        return
    reason = structure.blocked(_item_rows(ctx)).get(slug) or "not executable"
    raise WriteRefused(
        "INVALID_STATE",
        f"{slug!r} cannot be dispatched: {reason}",
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
        "SELECT slug, category FROM docs WHERE primitive = 'item' AND broken = 0"
    ).fetchall()
    return [
        structure.ItemRow(
            slug=name,
            category=category,
            part_of=tuple(sorted(edges.get(name, {}).get("part_of", ()))),
            depends_on=tuple(sorted(edges.get(name, {}).get("depends_on", ()))),
            last_activity_ms=None,
        )
        for name, category in rows
    ]


def _checkout(ctx: Context) -> tuple[Path, Path]:
    located = worktree_git_dir(ctx.store)
    if located is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "a dispatch needs the canonical store inside a Git worktree",
            {"store": str(ctx.store)},
        )
    return located[1].resolve(), located[0]


def _current_branch(root: Path) -> str:
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return result["stdout"].strip()


def _move_active(ctx: Context, slug: str, actor: str) -> None:
    rel = resolve_unique(ctx.index, slug)
    data, doc = read_doc(ctx.store, rel)
    if category_of(ctx.pack, doc.status or "") == "active":
        return
    target = _status_in(ctx.pack.statuses, "active")
    move_item(
        ctx,
        {
            "slug": slug,
            "status": target,
            "expect": etag(data),
            "resolution": None,
            "confirm": None,
            "key": None,
        },
        actor,
    )


def _move_blocked(ctx: Context, slug: str, actor: str) -> None:
    rel = resolve_unique(ctx.index, slug)
    data, doc = read_doc(ctx.store, rel)
    if category_of(ctx.pack, doc.status or "") == "blocked":
        return
    target = _status_in(ctx.pack.statuses, "blocked")
    move_item(
        ctx,
        {
            "slug": slug,
            "status": target,
            "expect": etag(data),
            "resolution": None,
            "confirm": None,
            "key": None,
        },
        actor,
    )


def _status_in(statuses: dict[str, str], category: str) -> str:
    for status, found in statuses.items():
        if found == category:
            return status
    raise WriteRefused(
        "VALIDATION_FAILED",
        f"the pack declares no {category!r} status",
        {"category": category},
    )


def _store_paths(ctx: Context, root: Path) -> tuple[str, ...]:
    try:
        rel = Path(ctx.store).resolve().relative_to(root).as_posix()
    except ValueError:
        rel = Path(ctx.store).name
    return tuple([rel, *ADAPTER_PATHS])


def _checkpoint(
    ctx: Context, root: Path, store_paths: tuple[str, ...], actor: str
) -> str:
    """Commit only store-owned work so the new worktree inherits instructions.

    Anything dirty outside the store and its adapters is refused: a dispatch
    never stashes, discards or commits a human's unrelated work.
    """
    entries = _status_entries(root)
    if not entries:
        return git_runner.rev_parse_head(root, root)
    foreign = [path for path in entries if not _inside(path, store_paths)]
    if foreign:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout has changes outside the store",
            {"paths": foreign[:20]},
        )
    present = [path for path in store_paths if (root / path).exists()]
    if not present:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout has no store folder to checkpoint",
            {"paths": entries[:20]},
        )
    _git(["add", "-A", "--", *present], root)
    _git(["commit", "-m", CHECKOUT_MESSAGE], root)
    return git_runner.rev_parse_head(root, root)


def _status_entries(root: Path) -> list[str]:
    paths: list[str] = []
    for entry in git_runner.status_porcelain(root, root):
        paths.append(entry[3:] if len(entry) > 3 and entry[2] == " " else entry)
    return sorted(path.replace("\\", "/") for path in paths if path)


def _inside(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


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


def _create_worktree(
    root: Path, paths: local_dispatch.DispatchPaths, default_branch: str, base: str
) -> None:
    existing = _git(["branch", "--list", paths.branch], root)
    if existing["stdout"].strip():
        raise WriteRefused(
            "CONFLICT",
            f"the dispatch branch {paths.branch!r} already exists",
            {"branch": paths.branch},
        )
    if paths.worktree.exists():
        if any(paths.worktree.iterdir()):
            raise WriteRefused(
                "CONFLICT",
                "the dispatch worktree directory already exists",
                {"worktree": str(paths.worktree)},
            )
        paths.worktree.rmdir()
    paths.root.mkdir(parents=True, exist_ok=True)
    added = _git(
        ["worktree", "add", "-b", paths.branch, str(paths.worktree), default_branch],
        root,
        timeout=120.0,
    )
    if added["exit"] != 0:
        detail = (added["stderr"] or added["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR",
            f"git worktree add failed: {detail or 'no output'}",
            {"branch": paths.branch, "worktree": str(paths.worktree)},
        )
    head = git_runner.rev_parse_head(paths.worktree, paths.worktree)
    if head != base:
        raise WriteRefused(
            "PROCESS_ERROR",
            "the new worktree is not at the captured base commit",
            {"expected": base, "actual": head},
        )


def _git(args: list[str], cwd: Path, timeout: float = 120.0) -> dict[str, Any]:
    return git_runner.git(args, cwd=cwd, confine_to=cwd, timeout=timeout)


# ── work order ────────────────────────────────────────────────────────────


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
        "objective": _bounded(f"{title}\n\n{doc.body}", 6000),
        "selected scope": scope,
        "conventions": (
            "Follow the repository's own conventions. Write the failing test first, "
            "watch it fail for the right reason, then make it pass."
        ),
        "constraints": (
            f"One session, at most {plan.max_attempts} attempt(s) and "
            f"{int(plan.budget.seconds or BUILTIN_SECONDS)} seconds. Never stage files to make "
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
            f"Never write {plan.store.absolute()} (the canonical store). Read it with "
            f"`rail --store {plan.store.absolute()}`; never adopt a store copied into your "
            "checkout. The parent writes Records from your result; a changed store path "
            "refuses your candidate."
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


def _bounded(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 16] + "\n[truncated]"


# ── execution ─────────────────────────────────────────────────────────────


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
    deadline = max(1.0, float(plan.budget.seconds or BUILTIN_SECONDS) - plan.used_seconds)

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
                failures.append("stderr: " + _bounded(stderr, 800))
            summary = _bounded("\n".join(failures), SUMMARY_MAX)
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
    report_id = _append_report(plan, report_data)
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
    if changes["store_changed"]:
        reasons.append(
            "the worker changed store-owned or adapter paths: "
            + ", ".join(changes["store_changed"])
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
    try:
        return checks_handler.verify(
            plan.ctx,
            {
                "target": plan.slug,
                "worktree": str(plan.paths.worktree),
                "verdict": None,
                "evidence": [],
                "confirm": None,
                "key": None,
            },
            plan.actor,
        )
    except (WriteRefused, ModelError) as error:
        return {"passed": False, "verdicts": [], "error": error.message}


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
        "what_happened": _bounded(
            f"the dispatch for {plan.slug!r} did not verify", SUMMARY_MAX
        ),
        "expected": "the declared Checks pass on the candidate commit",
        "observed": _bounded(observed, SUMMARY_MAX),
        "impact": "medium",
        "evidence": verdicts[:20],
        "fingerprint": None,
    }


def _capture_payload(capture: dict[str, Any], output_omitted: int) -> dict[str, Any]:
    """The capture object, incomplete when either list or output was cut."""
    omitted = int(capture.get("omitted") or 0) + int(output_omitted or 0)
    reason = CAPTURE_REASON if output_omitted else capture.get("reason")
    return {
        "complete": bool(capture.get("complete")) and omitted == 0,
        "reason": reason,
        "omitted": omitted,
    }


def _candidate(plan: _Plan) -> str | None:
    try:
        head = git_runner.rev_parse_head(plan.paths.worktree, plan.paths.worktree)
    except WriteRefused:
        return None
    return None if head == plan.base else head


def _append_report(plan: _Plan, data: dict[str, Any]) -> str | None:
    parse_report(data)
    result = commit(
        plan.store,
        plan.ctx.manifest,
        [],
        {"kind": "report.filed", "about": [plan.slug], "data": data},
        actor=plan.actor,
        index=plan.ctx.index,
    )
    return result.get("record")


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


def _rollback(store: Path, key: str, dispatch_id: str) -> None:
    """Forget a launch that never reached the droid."""
    local_dispatch.remove_binding(store, key)
    local_dispatch.progress_path(store, dispatch_id).unlink(missing_ok=True)


def _required(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused("VALIDATION_FAILED", f"dispatch needs {what}")
    return value.strip()


def _optional(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
