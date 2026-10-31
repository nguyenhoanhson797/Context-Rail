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

from context_rail.core.util import sha256_hex
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
- `rail cto brief` shows what is executable and what needs a human.
- `rail dispatch <item>` runs one Item in an isolated worktree; `rail review`
  and `rail merge` gate its result.
- `rail check` declares proof and `rail verify` runs it. Unproven work is not done.
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
    targets = _plan(root, skills, profiles)
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
        "harnesses": [],
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


def _plan(root: Path, skills: list[_Skill], profiles: list[_Profile]) -> list[_Target]:
    """Every adapter target, rendered in a deterministic order.

    Skills render first so a marker-bearing skill body is refused by name,
    before the block that would embed it is assembled.
    """
    rendered: list[tuple[str, str, str, bytes | None]] = []
    for skill in skills:
        text, digest = adapters.render_skill(skill.name, skill.text)
        rel = adapters.adapter_rel("skill", skill.name)
        rendered.append((rel, text, digest, _read(adapters.resolve_adapter(root, rel))))
    agents_rel = adapters.AGENTS_PATH
    agents_path = adapters.resolve_adapter(root, agents_rel)
    existing = _read(agents_path)
    existing_text, undecodable = _decode(existing, agents_rel)
    payload = _agents_payload(skills, profiles)
    conflict = undecodable
    agents_text, agents_digest = "", ""
    if conflict is None:
        try:
            agents_text, agents_digest = adapters.render_agents(existing_text, payload)
        except adapters.AdapterConflict as exc:
            conflict = str(exc)
    targets = [
        _Target(
            rel=agents_rel,
            path=agents_path,
            new_text=agents_text,
            digest=agents_digest,
            existing_bytes=existing,
            existing_text=existing_text,
            conflict=conflict,
        )
    ]
    for rel, text, digest, current in rendered:
        current_text, unreadable = _decode(current, rel)
        targets.append(
            _Target(
                rel=rel,
                path=adapters.resolve_adapter(root, rel),
                new_text=text,
                digest=digest,
                existing_bytes=current,
                existing_text=current_text,
                whole_file=True,
                conflict=unreadable,
            )
        )
    return targets


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
        "This project runs through Context Rail. The store beside this file is the",
        "truth: every change goes through `rail`, and its Records are immutable.",
        "",
        "## How work happens",
        "",
        _COMMANDS.rstrip("\n"),
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
