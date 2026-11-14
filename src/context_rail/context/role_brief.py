"""The role block: who acts as a role, the rules it follows and the skills it reads.

One renderer serves every brief that names a role. It reads the store's own
copies of the profile and skill notes and never writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from context_rail.model.cto import Profile, parse_profile
from context_rail.model.errors import ModelError
from context_rail.model.manifest import render_path
from context_rail.store.doc import Doc, parse_doc
from context_rail.store.location import PathEscape, confine

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["RoleGuide", "SkillGuide", "acting", "render_role", "role_guide"]


@dataclass(frozen=True)
class SkillGuide:
    """One skill a role reads: where its note lives and what it says."""

    name: str
    path: str
    version: int | None
    description: str
    body: str
    missing: bool


@dataclass(frozen=True)
class RoleGuide:
    """The profile and the skills behind one role, as the store holds them."""

    role: str
    profile_path: str | None
    profile_version: int | None
    rules: str
    skills: tuple[SkillGuide, ...]
    profile: Profile | None


def role_guide(ctx: Context, role: str) -> RoleGuide:
    """The profile and skills ``role`` acts under; reads only, never writes.

    In a store without roles the profile is the Actor profile named ``role``. In
    a store with roles it is the caller's seat profile when that seat holds the
    role, and there is none otherwise. Pinned skills come first, then the skills
    the pack's role declares.
    """
    found = _profile_document(ctx, role)
    profile: Profile | None = None
    if found is not None:
        profile = parse_profile(found.fields, body=found.body, role_mode=ctx.role_mode)
    skills: list[SkillGuide] = []
    pinned: set[str] = set()
    if profile is not None:
        for declared in profile.skills:
            name = PurePosixPath(declared).stem
            pinned.add(name)
            skills.append(_pinned_skill(ctx, declared, name))
    if ctx.role_mode:
        declared_role = ctx.pack.operating.roles.get(role)
        if declared_role is not None:
            for slug in declared_role.skills:
                if slug not in pinned:
                    skills.append(_role_skill(ctx, slug))
    return RoleGuide(
        role=role,
        profile_path=None if found is None else found.path,
        profile_version=None if found is None else _version(found.fields),
        rules="" if found is None else found.body.strip(),
        skills=tuple(skills),
        profile=profile,
    )


def acting(guide: RoleGuide) -> dict[str, Any]:
    """The role block as data, so a tool can show it without parsing text."""
    return {
        "role": guide.role,
        "profile": guide.profile_path,
        "profile_version": guide.profile_version,
        "skills": [
            {
                "name": skill.name,
                "path": skill.path,
                "version": skill.version,
                "missing": skill.missing,
            }
            for skill in guide.skills
        ],
    }


def render_role(guide: RoleGuide, limit: int, *, inline_skills: bool = True) -> str:
    """The role block, within ``limit`` characters.

    Skill bodies go in full first. Over the limit they collapse to a name, a
    description and a read command; then the rules are clipped beside a read
    command for the profile; then the rules line drops out, since the header
    already names the profile path; then the descriptions go; and only then is
    the text cut. A line that starts with ``#`` gains ``##`` so a body heading
    never reads as a brief section.
    """
    head = _head(guide)
    rules = _demote(guide.rules)
    pointer = _profile_pointer(guide)
    if inline_skills:
        full = _assemble(head, rules, _inline_block(guide.skills))
        if len(full) <= limit:
            return full
    collapsed = _skills_block(guide.skills, description=True)
    full = _assemble(head, rules, collapsed)
    if len(full) <= limit:
        return full
    clipped = _clipped_rules(head, rules, collapsed, pointer, limit)
    if clipped is not None:
        return clipped
    bare = _assemble(head, "", collapsed)
    if len(bare) <= limit:
        return bare
    terse = _skills_block(guide.skills, description=False)
    clipped = _clipped_rules(head, rules, terse, pointer, limit)
    if clipped is not None:
        return clipped
    return _clip(_assemble(head, "", terse), limit)


def _profile_document(ctx: Context, role: str) -> Doc | None:
    if not ctx.role_mode:
        return ctx.profile_document(role)
    seat = ctx.identity.seat
    if seat is None or seat.role != role or seat.profile is None:
        return None
    return ctx.profile_document(seat.profile)


def _pinned_skill(ctx: Context, declared: str, name: str) -> SkillGuide:
    data = _read_note(ctx.store, declared)
    if data is None:
        return _missing(name, declared)
    return _present(name, declared, data)


def _role_skill(ctx: Context, slug: str) -> SkillGuide:
    row = ctx.index.conn.execute(
        "SELECT path FROM docs WHERE slug = ? AND preset = 'skill' AND broken = 0 "
        "ORDER BY path LIMIT 1",
        (slug,),
    ).fetchone()
    if row is None:
        return _missing(slug, _skill_path(ctx, slug))
    path = row[0]
    data = _read_note(ctx.store, path)
    if data is None:
        return _missing(slug, path)
    return _present(slug, path, data)


def _read_note(store: Path, rel: str) -> bytes | None:
    try:
        return confine(store, rel).read_bytes()
    except (FileNotFoundError, PathEscape):
        return None


def _present(name: str, path: str, data: bytes) -> SkillGuide:
    doc = parse_doc(path, data)
    return SkillGuide(
        name=name,
        path=path,
        version=_version(doc.fields),
        description=_description(doc.fields),
        body=doc.body.strip(),
        missing=False,
    )


def _missing(name: str, path: str) -> SkillGuide:
    return SkillGuide(
        name=name, path=path, version=None, description="", body="", missing=True
    )


def _skill_path(ctx: Context, slug: str) -> str:
    try:
        return render_path(ctx.manifest, primitive="note", preset="skill", slug=slug)
    except ModelError:
        return slug


def _version(fields: Mapping[str, Any]) -> int | None:
    value = fields.get("version")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _description(fields: Mapping[str, Any]) -> str:
    value = fields.get("description")
    return " ".join(value.split()) if isinstance(value, str) else ""


def _head(guide: RoleGuide) -> str:
    line = f"You are acting as: {guide.role}"
    if guide.profile_path is None:
        return line
    version = "" if guide.profile_version is None else f" v{guide.profile_version}"
    return f"{line} (profile {guide.profile_path}{version})"


def _profile_slug(path: str) -> str:
    leaf = PurePosixPath(path)
    return leaf.parent.name if leaf.name == "profile.md" else leaf.stem


def _profile_pointer(guide: RoleGuide) -> str | None:
    if guide.profile_path is None:
        return None
    slug = _profile_slug(guide.profile_path)
    return f"(read: rail read show {slug} --path {guide.profile_path})"


def _demote(text: str) -> str:
    return "\n".join(
        "##" + line if line.startswith("#") else line for line in text.splitlines()
    )


def _skill_line(skill: SkillGuide, *, description: bool) -> str:
    if skill.missing:
        return f"- {skill.path}: missing (run rail ops upgrade)"
    name = f"- {skill.name}"
    if description and skill.description:
        name = f"{name}: {skill.description}"
    return f"{name} (read: rail read show {skill.name} --path {skill.path})"


def _inline_block(skills: tuple[SkillGuide, ...]) -> str:
    if not skills:
        return ""
    parts = [
        _skill_line(skill, description=False)
        if skill.missing
        else f"### {skill.name}\n{_demote(skill.body)}".rstrip()
        for skill in skills
    ]
    return "Skills:\n" + "\n\n".join(parts)


def _skills_block(skills: tuple[SkillGuide, ...], *, description: bool) -> str:
    if not skills:
        return ""
    lines = [_skill_line(skill, description=description) for skill in skills]
    return "Skills:\n" + "\n".join(lines)


def _assemble(head: str, rules: str, block: str) -> str:
    return "\n".join(part for part in (head, rules, block) if part)


def _clipped_rules(
    head: str, rules: str, block: str, pointer: str | None, limit: int
) -> str | None:
    """The rules cut to fit beside ``block``, ending in the profile read command."""
    if not rules or pointer is None:
        return None
    room = limit - len(_assemble(head, "", block)) - 1
    tail = f"… {pointer}"
    keep = room - len(tail)
    if keep < 1:
        return None
    line = rules[:keep].rstrip() + tail
    return _assemble(head, line, block)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    return text[: limit - 1].rstrip() + "…"
