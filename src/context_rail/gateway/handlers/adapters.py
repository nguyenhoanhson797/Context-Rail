"""Generic managed instructions: ``AGENTS.md`` and ``.agents/skills`` via commit.

One sync renders the workspace skills and profiles into a single managed block
in the checkout's ``AGENTS.md`` and one ``SKILL.md`` per skill Note, then writes
only the targets whose bytes change through one :func:`store.write.commit`. A
hand edit inside an owned block or an unmarked skill file refuses the whole
batch: the first needs a human ``--force`` with the exact phrase, the second is
manual repair. A malformed marker pair is always manual repair. No refusal
writes anything, and unmanaged prose is left byte-for-byte alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import dump_frontmatter, sha256_hex
from context_rail.gateway.handlers.common import is_skill_path
from context_rail.model.errors import ModelError
from context_rail.store import adapters
from context_rail.store.doc import Doc, etag, parse_doc
from context_rail.store.location import PathEscape
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["sync"]

RECORD_KIND = "adapters.synced"
AGENTS = "AGENTS.md"

_COMMANDS = """\
- `rail overview` shows what is executable and what needs a human.
- `rail loop dispatch <item>` runs one Item in an isolated worktree; `rail loop review`
  and `rail loop merge` gate its result, each with its own run and `--key`.
- `rail check declare` declares proof and `rail check run` runs it. Unproven work is not done.
- Human-only actions need the exact phrase the command prints.
"""


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


def sync(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Render and write the managed instruction files that changed."""
    store = ctx.store
    root = adapters.project_root(store)
    forced = args["force"] is True
    dry_run = args["dry_run"] is True

    skills, profiles, sources = _sources(store, ctx)
    requested = args.get("harness") or []
    harnesses = _harnesses(requested)
    targets = _plan(root, skills, profiles, harnesses)
    written: list[str] = []
    unchanged: list[str] = []
    refused: list[str] = []
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

    if refused and not dry_run:
        raise WriteRefused(
            "CONFLICT",
            "generated adapter files changed outside rail; repair them by hand or, "
            "for a hand-edited block, let a human force the rewrite",
            {"refused": refused, "reasons": reasons},
        )
    if dry_run:
        return _value(written, unchanged, refused, None, dry_run=True)

    binding = _binding(
        sources,
        digests,
        refused,
        forced=forced,
        current={target.display: target.current_etag for target in targets},
        writes={
            target.display: etag(target.new_text.encode("utf-8"))
            for target in targets
            if target.display in expected
        },
    )
    key = _bound_key(args["key"], binding)
    saved = replay(store, key)
    if not written:
        return _value(
            [], unchanged, refused, saved["record"] if saved else None, dry_run=False
        )
    if saved is not None:
        return _value(saved["written"], [], [], saved["record"], dry_run=False)

    ops = [
        Op(
            path=target.rel,
            data=target.new_text.encode("utf-8"),
            expect=expected[target.display],
        )
        for target in targets
        if target.display in expected
    ]
    data = {
        "schema": 1,
        "paths": [target.display for target in targets],
        "payload_digests": {target.display: target.digest for target in targets},
        "source_etags": sources,
        "harnesses": list(harnesses) if requested else [],
        "binding": binding,
    }
    result = commit(
        store,
        ctx.manifest,
        ops,
        {"kind": RECORD_KIND, "data": data},
        actor=actor,
        key=key,
        index=ctx.index,
    )
    return _value(result["written"], unchanged, [], result["record"], dry_run=False)


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
    written: list[str],
    unchanged: list[str],
    refused: list[str],
    record: str | None,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "written": written,
        "unchanged": unchanged,
        "refused": refused,
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
) -> list[_Target]:
    """Every adapter target, rendered in a deterministic order."""
    targets: list[_Target] = []
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
                _agents_payload(skills, profiles),
            )
        )
    if "claude" in harnesses:
        payload = _agents_payload(skills, profiles)
        targets.append(_managed(root, adapters.adapter_rel("claude"), payload))
        for profile in profiles:
            if profile.name not in adapters.CLAUDE_ROLES:
                continue
            model = profile.doc.fields.get("model")
            front = {
                "name": f"context-rail-{profile.name}",
                "description": f"Context Rail {profile.name} role",
                "model": model if isinstance(model, str) and model else "inherit",
            }
            frontmatter = "---\n" + dump_frontmatter(front) + "---\n"
            body = f"# Context Rail {profile.name}\n\n{profile.doc.body.strip(chr(10))}\n"
            targets.append(
                _managed(
                    root,
                    adapters.adapter_rel("claude-agent", profile.name),
                    body,
                    frontmatter=frontmatter,
                )
            )
    if "cursor" in harnesses:
        frontmatter = "---\ndescription: Context Rail store rules\nalwaysApply: true\n---\n"
        targets.append(
            _managed(
                root,
                adapters.adapter_rel("cursor"),
                _agents_payload(skills, profiles),
                frontmatter=frontmatter,
            )
        )
    if "gemini" in harnesses:
        targets.append(
            _managed(
                root,
                adapters.adapter_rel("gemini"),
                _agents_payload(skills, profiles),
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


def _agents_payload(skills: list[_Skill], profiles: list[_Profile]) -> str:
    lines = [
        "# Context Rail",
        "",
        "This project runs through Context Rail. The store folder beside this file",
        "is the truth: every change goes through `rail`, the SQLite index is only a",
        "cache, and its Records are immutable.",
        "",
        "## Read before you act",
        "",
        "Never parse the store's `pack.yaml`, `rail.yaml` or the charter to infer",
        "the workflow. Ask the compiled surface:",
        "",
        "| Question | Command |",
        "| --- | --- |",
        "| What kinds of work exist? | `rail types` |",
        "| What is executable now? | `rail next` |",
        "| What is this item's workflow and proof? | `rail show <slug>` |",
        "| What proof is missing, and how do I produce it? | `rail why <slug>` |",
        "| What does a preset, workflow or action mean? | `rail explain <name>` |",
        "| What is the operating model? | `rail model` |",
        "| What needs a human first? | `rail overview` |",
        "| What is blocked, and why? | `rail read status` |",
        "| Is the store governable right now? | `rail gate` |",
        "",
        "`rail next --all` reaches every item preset; `rail next --into <container>`",
        "overrides the focus for one call; `rail next --focus-only` stays inside it.",
        "",
        "## The protocol for every write",
        "",
        "- Read first, then pass the etag: `--expect <etag>`. A stale write is",
        "  refused with `CONFLICT`; re-read and retry.",
        "- Pass `--key <idempotency-key>` so a retry returns the first result.",
        "- Pass `--rel part_of=<slug>` and `--set <field>=<value>`. Never pass a",
        "  path, a parent folder or a final title.",
        "- Change status only with `rail change move`, or with a declared",
        "  `rail do <action>`.",
        "- Do not edit `records/`; they are immutable. Do not edit or delete the",
        "  SQLite index; run `rail ops reindex` if reads disagree with the files.",
        "- Every command prints one JSON envelope and exits 0 on success, 1 on a",
        "  refusal, 2 on a failure.",
        "",
        "## Human-only",
        "",
        "`approve`, `ops migrate` and `ops pack use` are human-only tools. They need",
        "an explicitly named human (`--actor human` or `RAIL_ACTOR=human`) and the",
        "typed phrase the command prints. Do not approve, lower a gate or switch a",
        "pack on the human's behalf.",
        "",
        "## When a command refuses",
        "",
        "Read `error.code` and `error.details`; the refusal names the fix. `CONFLICT`",
        "means re-read and retry. `AMBIGUOUS_FOCUS` lists candidates: choose one with",
        "`--into`, never guess. `NO_FOCUS` means pass `--rel part_of=<container>` or",
        "start the container. A `stale` check is not a pass: run `rail check run`",
        "again. `approval_mismatch` needs a human reapproval. If reads disagree with",
        "files, run `rail ops reindex`.",
        "",
        "## The loop",
        "",
        _COMMANDS.rstrip("\n"),
        "",
        "`rail loop tick --key <key>` is one bounded CTO session behind a",
        "single-instance lock; it must be the outer session.",
        "",
        "## Transports",
        "",
        "The same tool table is available over MCP stdio (`rail --actor cto serve",
        "mcp`, or `rail serve mcp` from inside the project) and in a loopback UI",
        "with `rail serve ui`. MCP keeps the canonical tool",
        "names (`status`, `next`, `show`, `add`, `move`, `dispatch`, `review`,",
        "`merge`, ...).",
        "",
        "## Profiles",
        "",
    ]
    if profiles:
        lines.extend(f"- `{profile.name}` — {profile.title}" for profile in profiles)
    else:
        lines.append("No workspace profiles yet.")
    lines.extend(
        [
            "",
            "## Skills",
            "",
            "The workspace skills are mirrored under `.agents/skills/`; read the one you",
            "are given before you start.",
            "",
        ]
    )
    for skill in skills:
        description = skill.doc.fields.get("description")
        lines.extend(
            [
                f"### {skill.name}",
                "",
                description if isinstance(description, str) else "",
                "",
                skill.doc.body.strip("\n"),
                "",
            ]
        )
    return "\n".join(lines).rstrip("\n") + "\n"


def _sources(
    store: Path, ctx: Context
) -> tuple[list[_Skill], list[_Profile], dict[str, str]]:
    """The workspace skills and profiles, with their source etags."""
    skills: list[_Skill] = []
    profiles: list[_Profile] = []
    sources: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}
    rows = ctx.index.conn.execute(
        "SELECT path, primitive FROM docs WHERE primitive IN ('note', 'actor') ORDER BY path"
    ).fetchall()
    for path, primitive in rows:
        if primitive == "note":
            if not is_skill_path(path):
                continue
            skill = _skill(store, path)
            if skill.name in names:
                first_path, first_text = names[skill.name]
                if skill.text != first_text:
                    raise WriteRefused(
                        "CONFLICT",
                        f"skill {skill.name!r} is exported from two sources",
                        {"name": skill.name, "paths": sorted([first_path, path])},
                    )
                sources[path] = skill.doc.etag
                continue
            names[skill.name] = (path, skill.text)
            skills.append(skill)
            sources[path] = skill.doc.etag
        elif primitive == "actor" and path.startswith("workspace/"):
            profile = _profile(store, path)
            profiles.append(profile)
            sources[path] = profile.doc.etag
    return skills, profiles, sources


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
