"""Generic managed instructions: ``AGENTS.md`` and ``.agents/skills`` via commit.

One sync renders the workspace skills and profiles into a single managed block
in the checkout's ``AGENTS.md`` and one ``SKILL.md`` per skill Note, then writes
only the targets whose bytes change through one :func:`store.write.commit`. A
hand edit inside an owned block or an unmarked skill file refuses the whole
batch: the first needs a human ``--force`` with the exact phrase, the second is
manual repair. A malformed marker pair is always manual repair. No refusal
writes anything, and unmanaged prose is left byte-for-byte alone.

The managed blocks point at the live briefs: they index each skill by name and
description, so a skill body lives only in its ``SKILL.md`` mirror. A target the
latest record lists and this sync no longer renders is deleted while its managed
digest still matches that record; a drifted one refuses the batch unless a human
forces it. Text outside the managed block is never deleted: it refuses the batch,
or with ``--force`` the file is kept, reported under ``kept``, and released from
the record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.context.work_brief import NAME_MODE_ROLES
from context_rail.core.util import dump_frontmatter, sha256_hex
from context_rail.gateway.delegation import (
    DELEGATION_USED,
    is_delegated,
    used_record,
)
from context_rail.gateway.delegation import (
    summary as delegation_summary,
)
from context_rail.gateway.handlers.common import is_skill_path
from context_rail.model.errors import ModelError
from context_rail.store import adapters
from context_rail.store.doc import Doc, etag, parse_doc
from context_rail.store.location import PathEscape
from context_rail.store.query import record_data
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["SkillConflict", "render_conflicts", "sync"]

RECORD_KIND = "adapters.synced"
AGENTS = "AGENTS.md"


def _commands(roles: tuple[str, ...]) -> str:
    """The record loop, naming the roles that take a brief in this store."""
    names = ", ".join(roles)
    return (
        f"- `rail overview` shows work and human waits; `rail loop brief <item> --role <role>` "
        f"gives the work order ({names}) for your own tool/sub-agents.\n"
        "- Research/commitless work: `rail loop result <item> --body-file <md>` (never fabricate a commit).\n"
        "- Code work: commit, `rail loop candidate <item> --commit <sha>`, then `rail loop result <item> --body-file <md> --commit <sha>`.\n"
        "- `rail check run <item>`, separate reviewer `rail loop brief <item> --role <role>` + "
        "`rail loop review <item> --verdict approve|reject --notes-file <md>`, then `rail change move <item> <status>`.\n"
        "- `rail loop report <item> --report-file <json>` logs diagnostics; human-only actions need the printed confirm phrase."
    )


@dataclass(frozen=True)
class _Skill:
    """One workspace skill Note, read from its store file."""

    path: str
    doc: Doc
    text: str

    @property
    def name(self) -> str:
        value = self.doc.fields.get("name")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class _Profile:
    """One workspace Actor profile, used only for the block's role list."""

    path: str
    doc: Doc

    @property
    def name(self) -> str:
        return self.doc.slug

    @property
    def title(self) -> str:
        return self.doc.title or f"Context Rail {self.doc.slug} role"


@dataclass(frozen=True)
class SkillConflict:
    """One skill name exported from more than one source with different text."""

    name: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class _Target:
    """One adapter file: its namespace path, absolute path and new bytes."""

    rel: str
    path: Path
    new_text: str
    digest: str
    existing_bytes: bytes | None
    existing_text: str | None
    whole_file: bool = False
    conflict: str | None = None

    @property
    def display(self) -> str:
        return self.rel[len(adapters.ADAPTER_PREFIX) :]

    @property
    def current_etag(self) -> str:
        return ABSENT if self.existing_bytes is None else etag(self.existing_bytes)


@dataclass(frozen=True)
class _Retirement:
    """A recorded target this sync no longer renders, and what becomes of it.

    ``verdict`` is ``remove`` (delete it), ``keep`` (leave it on disk and release
    its record entry), ``refuse`` (the batch stops) or ``gone`` (already absent,
    so the new record drops it).
    """

    rel: str
    display: str
    recorded: str
    current: str
    verdict: str
    reason: str | None = None


def sync(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Render and write the managed instruction files that changed."""
    store = ctx.store
    root = adapters.project_root(store)
    forced = args["force"] is True
    dry_run = args["dry_run"] is True

    skills, profiles, sources = _sources(store, ctx)
    requested = args.get("harness") or []
    harnesses = _harnesses(requested)
    targets = _plan(root, skills, profiles, harnesses, _roles(ctx))
    rendered = {target.display for target in targets}
    recorded = _recorded_digests(ctx)
    retirements = _retirements(root, rendered, harnesses, recorded, forced=forced)
    carried = {
        display: digest
        for display, digest in recorded.items()
        if display not in rendered and _harness_of(display) not in harnesses
    }
    written: list[str] = []
    unchanged: list[str] = []
    refused: list[str] = []
    removed: list[str] = []
    kept: list[str] = []
    reasons: dict[str, str] = {}
    expected: dict[str, str] = {}
    digests: dict[str, str] = {}
    for target in targets:
        state, current, reason = _reconcile(target, forced=forced)
        if state == "refused":
            refused.append(target.display)
            reasons[target.display] = reason or "the generated file cannot be replaced"
            continue
        digests[target.display] = target.digest
        if state == "unchanged":
            unchanged.append(target.display)
            continue
        written.append(target.display)
        expected[target.display] = current
    for retirement in retirements:
        if retirement.verdict == "remove":
            removed.append(retirement.display)
        elif retirement.verdict == "keep":
            kept.append(retirement.display)
        elif retirement.verdict == "refuse":
            refused.append(retirement.display)
            reasons[retirement.display] = retirement.reason or _ORPHAN_CHANGED
    released = [
        retirement.display
        for retirement in retirements
        if retirement.verdict in ("gone", "keep")
    ]

    if refused and not dry_run:
        raise WriteRefused(
            "CONFLICT",
            "generated adapter files changed outside rail; repair them by hand or, "
            "for a hand-edited block, let a human force the rewrite",
            {"refused": refused, "reasons": reasons},
        )
    if dry_run:
        return _value(
            written=written,
            unchanged=unchanged,
            refused=refused,
            removed=removed,
            kept=kept,
            record=None,
            dry_run=True,
        )

    binding = _binding(
        sources,
        {**digests, **carried},
        refused,
        forced=forced,
        current={
            **{target.display: target.current_etag for target in targets},
            **{retirement.display: retirement.current for retirement in retirements},
        },
        writes={
            **{
                target.display: etag(target.new_text.encode("utf-8"))
                for target in targets
                if target.display in expected
            },
            **{display: ABSENT for display in removed},
        },
    )
    key = _bound_key(args["key"], binding)
    saved = replay(store, key)
    if not written and not removed and not released:
        return _value(
            written=[],
            unchanged=unchanged,
            refused=refused,
            removed=[],
            kept=kept,
            record=saved["record"] if saved else None,
            dry_run=False,
        )
    if saved is not None:
        replayed = saved["written"]
        return _value(
            written=[name for name in replayed if name in rendered],
            unchanged=[],
            refused=[],
            removed=[name for name in replayed if name not in rendered],
            kept=kept,
            record=saved["record"],
            dry_run=False,
        )

    ops = [
        Op(
            path=target.rel,
            data=target.new_text.encode("utf-8"),
            expect=expected[target.display],
        )
        for target in targets
        if target.display in expected
    ]
    ops.extend(
        Op(path=retirement.rel, data=None, expect=retirement.current)
        for retirement in retirements
        if retirement.verdict == "remove"
    )
    data = {
        "schema": 1,
        "paths": [target.display for target in targets] + list(carried),
        "payload_digests": {**{target.display: target.digest for target in targets}, **carried},
        "source_etags": sources,
        "harnesses": list(harnesses) if requested else [],
        "binding": binding,
    }
    delegated = is_delegated(ctx, "ops adapters render")
    result = commit(
        store,
        ctx.manifest,
        ops,
        {"kind": RECORD_KIND, "data": data},
        actor=actor,
        key=key,
        index=ctx.index,
        extra_records=(used_record(ctx, "ops adapters render"),) if delegated else (),
        reserved_kinds=frozenset({DELEGATION_USED}) if delegated else frozenset(),
    )
    value = _value(
        written=written,
        unchanged=unchanged,
        refused=[],
        removed=removed,
        kept=kept,
        record=result["record"],
        dry_run=False,
    )
    if delegated:
        value["delegation"] = delegation_summary(ctx, "ops adapters render")
    return value


def _binding(
    sources: dict[str, str],
    digests: dict[str, str],
    refused: list[str],
    *,
    forced: bool,
    current: dict[str, str],
    writes: dict[str, str],
) -> str:
    """What this sync reconciles: its sources, its outputs and the target bytes.

    The rendered digests describe what rail would write, so on their own they
    say nothing about the file on disk. The current etag of every target and
    the bytes this pass would write both join the binding: a deleted or
    hand-edited target changes it, so a reused key performs the real work, while
    an identical repeat still matches and replays the saved outcome.
    """
    payload = json.dumps(
        {
            "sources": sources,
            "digests": digests,
            "refused": refused,
            "forced": forced,
            "current": current,
            "writes": writes,
        },
        sort_keys=True,
    )
    return sha256_hex(payload.encode("utf-8"))


def _bound_key(key: str | None, binding: str) -> str | None:
    """The idempotency key one reconciliation binds to, or None for no key."""
    if key is None:
        return None
    return f"{key}#{binding}"


def _value(
    *,
    written: list[str],
    unchanged: list[str],
    refused: list[str],
    removed: list[str],
    kept: list[str],
    record: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "written": written,
        "unchanged": unchanged,
        "refused": refused,
        "removed": removed,
        "kept": kept,
        "warnings": [],
        "record": record,
        "dry_run": dry_run,
    }


def _reconcile(target: _Target, *, forced: bool) -> tuple[str, str, str | None]:
    """``("write"|"unchanged"|"refused", current etag, refusal reason)``."""
    current = target.current_etag
    if target.conflict is not None:
        return "refused", current, target.conflict
    if target.existing_text is None:
        return "write", current, None
    if target.existing_text == target.new_text:
        return "unchanged", current, None
    existing = target.existing_text
    state = adapters.read_managed(existing)
    if state is None:
        if target.whole_file or adapters.START_MARK in existing or adapters.END_MARK in existing:
            # A malformed boundary is repaired by hand, never by a rewrite.
            return "refused", current, "its CONTEXT-RAIL markers do not pair exactly once"
        return "write", current, None
    payload, digest = state
    if adapters.payload_digest(payload) != digest:
        if forced:
            return "write", current, None
        return "refused", current, "its owned content changed outside rail"
    return "write", current, None


def _harnesses(raw: list[str]) -> tuple[str, ...]:
    """The requested harnesses, deduped in request order."""
    if not raw:
        return ("agents",)
    names = tuple(dict.fromkeys(raw))
    unknown = [name for name in names if name not in adapters.HARNESSES]
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "unknown harness name(s): " + ", ".join(unknown),
            {"harnesses": unknown, "allowed": list(adapters.HARNESSES)},
        )
    return names


def _plan(
    root: Path,
    skills: list[_Skill],
    profiles: list[_Profile],
    harnesses: tuple[str, ...],
    roles: tuple[str, ...],
) -> list[_Target]:
    """Every adapter target, rendered in a deterministic order."""
    targets: list[_Target] = []
    payload = _agents_payload(skills, profiles, roles)
    if "agents" in harnesses:
        for skill in skills:
            text, digest = adapters.render_skill(skill.name, skill.text)
            rel = adapters.adapter_rel("skill", skill.name)
            targets.append(
                _whole(root, rel, text, digest, adapters.resolve_adapter(root, rel))
            )
        targets.append(
            _managed(
                root,
                adapters.AGENTS_PATH,
                payload,
            )
        )
    if "claude" in harnesses:
        targets.append(_managed(root, adapters.adapter_rel("claude"), payload))
        for profile in profiles:
            if profile.name not in adapters.CLAUDE_ROLES:
                continue
            front = {
                "name": f"context-rail-{profile.name}",
                "description": f"Context Rail {profile.name} role",
                "model": "inherit",
            }
            frontmatter = "---\n" + dump_frontmatter(front) + "---\n"
            targets.append(
                _managed(
                    root,
                    adapters.adapter_rel("claude-agent", profile.name),
                    _claude_agent_body(profile.name),
                    frontmatter=frontmatter,
                )
            )
    if "cursor" in harnesses:
        frontmatter = "---\ndescription: Context Rail store rules\nalwaysApply: true\n---\n"
        targets.append(
            _managed(
                root,
                adapters.adapter_rel("cursor"),
                payload,
                frontmatter=frontmatter,
            )
        )
    if "gemini" in harnesses:
        targets.append(
            _managed(
                root,
                adapters.adapter_rel("gemini"),
                payload,
            )
        )
    return targets


def _managed(
    root: Path, rel: str, payload: str, *, frontmatter: str = ""
) -> _Target:
    path = adapters.resolve_adapter(root, rel)
    existing = _read(path)
    existing_text, undecodable = _decode(existing, rel)
    conflict = undecodable
    text, digest = "", ""
    if conflict is None:
        try:
            text, digest = adapters.render_managed(
                existing_text, payload, frontmatter=frontmatter
            )
        except adapters.AdapterConflict as exc:
            conflict = str(exc)
    return _Target(
        rel=rel,
        path=path,
        new_text=text,
        digest=digest,
        existing_bytes=existing,
        existing_text=existing_text,
        conflict=conflict,
    )


def _whole(
    root: Path, rel: str, text: str, digest: str, path: Path
) -> _Target:
    existing = _read(path)
    existing_text, unreadable = _decode(existing, rel)
    return _Target(
        rel=rel,
        path=path,
        new_text=text,
        digest=digest,
        existing_bytes=existing,
        existing_text=existing_text,
        whole_file=True,
        conflict=unreadable,
    )


def _decode(data: bytes | None, rel: str) -> tuple[str | None, str | None]:
    """One target's current text, or the reason its bytes cannot be rewritten.

    Unmanaged prose is not ours to normalize, so a file that is not valid UTF-8
    is refused by name rather than re-encoded with replacement characters: that
    would silently corrupt text a human typed.
    """
    if data is None:
        return None, None
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        display = rel[len(adapters.ADAPTER_PREFIX) :]
        return None, f"{display} is not valid UTF-8; convert it to UTF-8 by hand"


def _agents_payload(
    skills: list[_Skill], profiles: list[_Profile], roles: tuple[str, ...]
) -> str:
    profile_summary = (
        "; ".join(f"`{p.name}` ({p.title})" for p in profiles)
        if profiles
        else "none yet"
    )
    skill_summary = (
        "; ".join(_skill_line(skill) for skill in skills)
        if skills
        else "none yet"
    )
    lines = [
        "# Context Rail",
        "Store (`context-rail/`) is the truth; SQLite is a cache; Records in `records/` are immutable; JSON envelope exits 0/1/2.",
        "## Read before you act",
        "Never parse `pack.yaml`, `rail.yaml` or the charter. Ask: `rail types`, `rail next` (`--all`/`--into`/`--focus-only`), "
        "`rail read show <slug>`, `rail why <slug>`, `rail explain <name>`, `rail model`, `rail overview`, `rail read status`, `rail gate`.",
        "## Write, Human-only & Refusals",
        "- Read first and pass `--expect <etag>`, `--key <idempotency-key>`, `--rel part_of=<slug>`, `--set <field>=<value>` (no path/parent/title); change status only via `rail change move` or `rail do <action>`; run `rail ops reindex` after hand edits.",
        "- Human-only (`--actor human` or `RAIL_ACTOR=human` + printed phrase): `ops migrate`, `ops upgrade`, `ops pack use`. `approve` records its signer; the charter's `approval.agents` says whether an agent may sign. Never claim a human signature you did not receive, and never lower a gate.",
        "- Refusals: read `error.code`/`error.details` — `CONFLICT` re-read; `AMBIGUOUS_FOCUS` pass `--into`; `NO_FOCUS` pass `--rel part_of=` or start container; `stale` re-run `rail check run`; `approval_mismatch` reapprove `pack:<name>` then the charter (a named lead may sign when the charter allows it).",
        "## Start here",
        "- CTO owns design within charter and requires one registered item + Markdown result record per assignment (incl. discovery).",
        "- Coordinators: `rail overview`. Workers: `rail loop brief <item> --role <role>` for your role, rules, compiled result shape, and skills.",
        "## The loop",
        "The rail does not run agents; your tool runs sub-agents and merges in git. Rail keeps work order, proof and record:",
        _commands(roles),
        "## Transports, Profiles & Skills",
        "- MCP (`rail serve mcp`, canonical names `status|next|show|add|move|result|candidate|review|...`) and loopback UI (`rail serve ui`).",
        f"- Profiles: {profile_summary}.",
        f"- Skills (bodies live in your brief / `.agents/skills/`): {skill_summary}",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _sources(
    store: Path, ctx: Context
) -> tuple[list[_Skill], list[_Profile], dict[str, str]]:
    """The workspace skills and profiles, with their source etags."""
    skills, profiles, sources, conflicts = _collect_sources(store, ctx)
    if conflicts:
        conflict = conflicts[0]
        raise WriteRefused(
            "CONFLICT",
            f"skill {conflict.name!r} is exported from two sources",
            {"name": conflict.name, "paths": list(conflict.paths)},
        )
    return skills, profiles, sources


def render_conflicts(ctx: Context) -> list[SkillConflict]:
    """The skill names a render would refuse, with every source path."""
    return _collect_sources(ctx.store, ctx)[3]


def _collect_sources(
    store: Path, ctx: Context
) -> tuple[list[_Skill], list[_Profile], dict[str, str], list[SkillConflict]]:
    """Read every skill and profile, grouping skill names that would collide."""
    grouped: dict[str, list[_Skill]] = {}
    profiles: list[_Profile] = []
    sources: dict[str, str] = {}
    rows = ctx.index.conn.execute(
        "SELECT path, primitive FROM docs WHERE primitive IN ('note', 'actor') ORDER BY path"
    ).fetchall()
    for path, primitive in rows:
        if primitive == "note":
            if not is_skill_path(path):
                continue
            skill = _skill(store, path)
            grouped.setdefault(skill.name, []).append(skill)
            sources[path] = skill.doc.etag
        elif primitive == "actor" and path.startswith("workspace/"):
            profile = _profile(store, path)
            profiles.append(profile)
            sources[path] = profile.doc.etag
    skills: list[_Skill] = []
    conflicts: list[SkillConflict] = []
    for name, found in grouped.items():
        if len({skill.text for skill in found}) > 1:
            conflicts.append(
                SkillConflict(name, tuple(sorted(skill.path for skill in found)))
            )
        else:
            skills.append(found[0])
    return skills, profiles, sources, conflicts


def _skill(store: Path, path: str) -> _Skill:
    data = _bytes(store, path)
    doc = parse_doc(path, data)
    if doc.problems:
        raise ModelError(
            "VALIDATION_FAILED", f"skill {path!r} does not parse: {doc.problems[0]}"
        )
    skill = _Skill(path=path, doc=doc, text=data.decode("utf-8-sig"))
    if not skill.name:
        raise ModelError("VALIDATION_FAILED", f"skill {path!r} has no name")
    if skill.name != doc.slug:
        raise ModelError(
            "VALIDATION_FAILED",
            f"skill {path!r} name must equal its file stem {doc.slug!r}",
        )
    try:
        adapters.adapter_rel("skill", skill.name)
    except PathEscape as exc:
        raise ModelError("VALIDATION_FAILED", str(exc)) from exc
    return skill


def _profile(store: Path, path: str) -> _Profile:
    return _Profile(path=path, doc=parse_doc(path, _bytes(store, path)))


def _bytes(store: Path, path: str) -> bytes:
    try:
        return (store / path).read_bytes()
    except FileNotFoundError as exc:
        raise WriteRefused(
            "INVALID_STATE", f"{path!r} is indexed but missing", {"path": path}
        ) from exc


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


_ORPHAN_CHANGED = "orphan changed outside rail"
_ORPHAN_OUTSIDE = "orphan has text outside its managed block"
_ORPHAN_NO_BLOCK = "orphan has no paired managed block"
_ORPHAN_UNREADABLE = "orphan is not valid UTF-8"
_ORPHAN_NESTED = "orphan holds a marker line inside its managed block"
_ORPHAN_IO = "orphan cannot be read"


def _roles(ctx: Context) -> tuple[str, ...]:
    """The roles whose briefs this store serves: its declared roles, else the name-mode pair."""
    if ctx.role_mode:
        return tuple(ctx.pack.operating.roles)
    return NAME_MODE_ROLES


def _skill_line(skill: _Skill) -> str:
    description = skill.doc.fields.get("description")
    text = description if isinstance(description, str) else ""
    return f"`{skill.name}` ({text})".rstrip()


def _claude_agent_body(role: str) -> str:
    return (
        f"# Context Rail {role}\n\n"
        f"You act as the Context Rail {role}. Start with "
        f"`rail loop brief <item> --role {role}` (the CTO starts with `rail overview`) "
        "and follow its role section.\n"
    )


def _retirements(
    root: Path,
    rendered: set[str],
    harnesses: tuple[str, ...],
    recorded: dict[str, str],
    *,
    forced: bool,
) -> list[_Retirement]:
    """The recorded targets of the requested harnesses this sync no longer renders."""
    retired: list[_Retirement] = []
    for display, digest in recorded.items():
        if display in rendered or _harness_of(display) not in harnesses:
            continue
        rel = f"{adapters.ADAPTER_PREFIX}{display}"
        try:
            path = adapters.resolve_adapter(root, rel)
        except PathEscape:
            retired.append(_Retirement(rel, display, digest, ABSENT, "gone"))
            continue
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            retired.append(_Retirement(rel, display, digest, ABSENT, "gone"))
            continue
        except OSError:
            verdict, reason = _held(forced, _ORPHAN_IO)
            retired.append(_Retirement(rel, display, digest, ABSENT, verdict, reason))
            continue
        verdict, reason = _retirement_verdict(rel, data, digest, forced=forced)
        retired.append(_Retirement(rel, display, digest, etag(data), verdict, reason))
    return retired


def _retirement_verdict(
    rel: str, data: bytes, recorded: str, *, forced: bool
) -> tuple[str, str | None]:
    """What happens to a target no longer rendered: ``remove``, ``keep`` or ``refuse``.

    A file is deleted only when the text outside its managed block is exactly what
    rail renders there: a frontmatter block for a skill, a Claude agent or a Cursor
    rule, and nothing otherwise. A marker line inside the block is a human's too,
    since rail never renders one into a payload. Anything else is held: kept under
    a forced sync, refused otherwise. Inside the block, the payload must still match
    the digest the record lists; a drifted payload is deleted only by a forced sync.
    """
    display = rel[len(adapters.ADAPTER_PREFIX) :]
    text, _undecodable = _decode(data, rel)
    if text is None:
        return _held(forced, _ORPHAN_UNREADABLE)
    parts = adapters.fenced_parts(text)
    state = adapters.read_managed(text)
    if parts is None or state is None:
        return _held(forced, _ORPHAN_NO_BLOCK)
    prefix, suffix = parts
    payload, digest = state
    if adapters.carries_marker(payload):
        return _held(forced, _ORPHAN_NESTED)
    frontmatter = _takes_frontmatter(display)
    if suffix or not adapters.owned_prefix(prefix, frontmatter=frontmatter):
        return _held(forced, _ORPHAN_OUTSIDE)
    if digest == recorded and adapters.payload_digest(payload) == recorded:
        return "remove", None
    return ("remove", None) if forced else ("refuse", _ORPHAN_CHANGED)


def _held(forced: bool, reason: str) -> tuple[str, str | None]:
    """A target that must not be deleted: kept under a forced sync, refused otherwise."""
    return ("keep", None) if forced else ("refuse", reason)


def _takes_frontmatter(display: str) -> bool:
    """Whether rail renders a frontmatter block ahead of this target's managed block."""
    return display == adapters.CURSOR_REL or display.startswith(
        (f"{adapters.SKILLS_REL}/", f"{adapters.CLAUDE_AGENTS_REL}/")
    )


def _recorded_digests(ctx: Context) -> dict[str, str]:
    """The digests the newest adapters.synced record lists, by display path."""
    row = ctx.index.conn.execute(
        "SELECT d.path FROM docs d JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "WHERE k.value_json = ? ORDER BY d.record_at DESC, d.slug DESC LIMIT 1",
        (json.dumps(RECORD_KIND),),
    ).fetchone()
    if row is None:
        return {}
    digests = record_data(ctx.index, row[0]).get("payload_digests")
    if not isinstance(digests, dict):
        return {}
    return {
        display: digest
        for display, digest in digests.items()
        if isinstance(display, str) and isinstance(digest, str)
    }


def _harness_of(display: str) -> str | None:
    """The harness whose sync renders this display path, when one does."""
    if display == adapters.AGENTS_REL or display.startswith(f"{adapters.SKILLS_REL}/"):
        return "agents"
    if display == adapters.CLAUDE_REL or display.startswith(f"{adapters.CLAUDE_AGENTS_REL}/"):
        return "claude"
    if display == adapters.CURSOR_REL:
        return "cursor"
    if display == adapters.GEMINI_REL:
        return "gemini"
    return None
