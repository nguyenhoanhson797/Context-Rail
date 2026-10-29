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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    whole_file: bool = False
    conflict: str | None = None

    @property
    def display(self) -> str:
        return self.rel[len(adapters.ADAPTER_PREFIX) :]

    @property
    def existing(self) -> str | None:
        if self.existing_bytes is None:
            return None
        return self.existing_bytes.decode("utf-8", "replace")


def sync(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Render and write the managed instruction files that changed."""
    store = ctx.store
    root = adapters.project_root(store)
    forced = args["force"] is True
    dry_run = args["dry_run"] is True

    saved = replay(store, args["key"])
    if saved is not None and not dry_run:
        return _value(saved["written"], [], [], saved["record"], dry_run=False)

    skills, profiles, sources = _sources(store, ctx)
    targets = _plan(root, skills, profiles)
    written: list[str] = []
    unchanged: list[str] = []
    refused: list[str] = []
    reasons: dict[str, str] = {}
    expected: dict[str, str] = {}
    for target in targets:
        state, current, reason = _reconcile(target, forced=forced)
        if state == "refused":
            refused.append(target.display)
            reasons[target.display] = reason or "the generated file cannot be replaced"
            continue
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
    if dry_run or not written:
        return _value(
            written if dry_run else [], unchanged, refused, None, dry_run=dry_run
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
    data = {
        "schema": 1,
        "paths": [target.display for target in targets],
        "payload_digests": {target.display: target.digest for target in targets},
        "source_etags": sources,
        "harnesses": [],
    }
    result = commit(
        store,
        ctx.manifest,
        ops,
        {"kind": RECORD_KIND, "data": data},
        actor=actor,
        key=args["key"],
        index=ctx.index,
    )
    return _value(result["written"], unchanged, [], result["record"], dry_run=False)


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
    current = ABSENT if target.existing_bytes is None else etag(target.existing_bytes)
    if target.conflict is not None:
        return "refused", current, target.conflict
    if target.existing_bytes is None:
        return "write", current, None
    existing = target.existing or ""
    if target.existing == target.new_text:
        return "unchanged", current, None
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
    """Every adapter target, rendered in a deterministic order."""
    agents_rel = adapters.AGENTS_PATH
    agents_path = adapters.resolve_adapter(root, agents_rel)
    existing = _read(agents_path)
    existing_text = None if existing is None else existing.decode("utf-8", "replace")
    payload = _agents_payload(skills, profiles)
    conflict: str | None = None
    try:
        agents_text, agents_digest = adapters.render_agents(existing_text, payload)
    except adapters.AdapterConflict as exc:
        agents_text, agents_digest, conflict = "", "", str(exc)
    targets = [
        _Target(
            rel=agents_rel,
            path=agents_path,
            new_text=agents_text,
            digest=agents_digest,
            existing_bytes=existing,
            conflict=conflict,
        )
    ]
    for skill in skills:
        rel = adapters.adapter_rel("skill", skill.name)
        path = adapters.resolve_adapter(root, rel)
        text, digest = adapters.render_skill(skill.name, skill.text)
        targets.append(
            _Target(
                rel=rel,
                path=path,
                new_text=text,
                digest=digest,
                existing_bytes=_read(path),
                whole_file=True,
            )
        )
    return targets


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
    names: dict[str, str] = {}
    rows = ctx.index.conn.execute(
        "SELECT path, primitive FROM docs WHERE primitive IN ('note', 'actor') ORDER BY path"
    ).fetchall()
    for path, primitive in rows:
        if primitive == "note":
            if not _is_skill_path(path):
                continue
            skill = _skill(store, path)
            if skill.name in names:
                raise WriteRefused(
                    "CONFLICT",
                    f"skill {skill.name!r} is exported from two sources",
                    {"name": skill.name, "paths": sorted([names[skill.name], path])},
                )
            names[skill.name] = path
            skills.append(skill)
            sources[path] = skill.doc.etag
        elif primitive == "actor" and path.startswith("workspace/"):
            profile = _profile(store, path)
            profiles.append(profile)
            sources[path] = profile.doc.etag
    return skills, profiles, sources


def _is_skill_path(path: str) -> bool:
    return path.startswith("workspace/") and "/skills/" in path


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
