"""A seat's brief: the parts its role declares, for the documents it may see.

The brief is built from the same index projections every other read uses. It
never widens a role: the anchor subtree, the ancestors it may look up and one
hop along the declared relations decide which documents appear, and a budget
cut is always named in the text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from context_rail.context import recipes
from context_rail.context.reach import ReachError, resolve_reach
from context_rail.context.role_brief import role_guide, role_tiers
from context_rail.core.util import now_iso
from context_rail.kernel import structure
from context_rail.kernel.reach import ancestors, descendants, one_hop
from context_rail.kernel.seats import LiveSeat
from context_rail.store import query

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.model.roles import Role

__all__ = ["seat_brief", "seat_brief_for"]

_EXTRA_ONLY = ("diff", "evidence", "code_area")
_BODY_CHARS = 200
_CANDIDATES = 3
_RECORDS = 3


def seat_brief(
    context: Context,
    seat: LiveSeat,
    role: Role,
    *,
    max_chars: int | None = None,
    extra_parts: Mapping[str, str] | None = None,
    with_role: bool = True,
) -> dict[str, Any]:
    """The bounded brief one seat may read, and what the budget left out."""
    documents = _documents(context, seat, role)
    documents = _within_reach(context, seat, role, documents)
    if seat.anchor in documents:
        documents.remove(seat.anchor)
        documents.insert(0, seat.anchor)
    profile = context.profile
    budget = role.sees.chars
    entries = role.sees.docs
    if profile is not None:
        budget = min(budget, int(profile.budget.get("max_chars", budget)))
        entries = min(entries, int(profile.budget.get("max_docs", entries)))
    if max_chars is not None:
        budget = min(budget, max_chars)
    documents = documents[:entries]
    lines = _lines(context, documents, role, extra_parts, seat)
    active = [part for part in role.sees.parts if lines.get(part)]
    header_line = f"# Seat {seat.seat} on {seat.anchor}"
    if with_role:
        guide = role_guide(context, seat.role)
        heads = [f"{header_line}\n\n{tier}" for tier in role_tiers(guide, budget // 3)]
        text, trimmed, dropped, fitted = _fit_heads(heads, active, lines, budget)
    else:
        text, trimmed, dropped, fitted, _ = _fit(header_line, active, lines, budget)
    return {
        "text": text,
        "estimated_tokens": recipes.estimate_tokens(text),
        "documents": documents,
        "parts": fitted,
        "trimmed": trimmed,
        "dropped": dropped,
        "seat": seat.seat,
        "anchor": seat.anchor,
    }


def seat_brief_for(
    context: Context,
    *,
    role_name: str,
    anchor: str,
    profile: str | None,
    granted_by: str,
    seat: str | None = None,
    extra_parts: Mapping[str, str] | None = None,
    max_chars: int | None = None,
    with_role: bool = True,
) -> dict[str, Any]:
    """The brief a role would read anchored at ``anchor``, with no live seat needed.

    A tool that works an item has no session to read the brief through, so the
    seat is stood up for this one read and never recorded. An unknown role is a
    ``LookupError``; callers name the roles the pack declares.
    """
    role = context.pack.operating.roles.get(role_name)
    if role is None:
        raise LookupError(f"unknown role {role_name!r}")
    live = LiveSeat(
        seat=seat or role_name,
        role=role_name,
        anchor=anchor,
        profile=profile,
        granted_by=granted_by,
        granted_at=now_iso(),
    )
    return seat_brief(
        context,
        live,
        role,
        max_chars=max_chars,
        extra_parts=extra_parts,
        with_role=with_role,
    )


def _documents(context: Context, seat: LiveSeat, role: Role) -> list[str]:
    index = context.index
    children: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for src, dst in index.conn.execute(
        "SELECT src, dst FROM relations WHERE rel = 'part_of'"
    ):
        children.setdefault(dst, []).append(src)
        parents.setdefault(src, []).append(dst)
    if role.anchor.kind == "project":
        return [
            slug
            for (slug,) in index.conn.execute(
                "SELECT slug FROM docs WHERE primitive IN ('item', 'note') "
                "AND broken = 0 ORDER BY slug"
            )
        ]
    anchor = seat.anchor
    found: set[str] = {anchor}
    found |= _below(anchor, children, role.sees.down)
    found |= ancestors(anchor, parents, role.sees.up)
    for relation in role.sees.relations:
        edges: dict[str, list[str]] = {}
        for src, dst in index.conn.execute(
            "SELECT src, dst FROM relations WHERE rel = ?", (relation,)
        ):
            edges.setdefault(src, []).append(dst)
        found |= one_hop(found, edges)
    return sorted(found)


def _below(
    anchor: str, children: Mapping[str, list[str]], depth: int | None
) -> frozenset[str]:
    if depth is None:
        return descendants(anchor, children)
    seen = {anchor}
    frontier = [anchor]
    for _ in range(depth):
        following: list[str] = []
        for node in frontier:
            for child in children.get(node, ()):
                if child not in seen:
                    seen.add(child)
                    following.append(child)
        frontier = following
        if not frontier:
            break
    return frozenset(seen)


def _within_reach(
    context: Context,
    seat: LiveSeat,
    role: Role,
    documents: list[str],
) -> list[str]:
    """Only the documents this seat's own role may read; the anchor always may."""
    try:
        reach = resolve_reach(context.index, context.pack, role, seat)
    except ReachError:
        return []
    kept: list[str] = []
    for slug in documents:
        if slug == seat.anchor:
            kept.append(slug)
            continue
        row = context.index.conn.execute(
            "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone()
        if row is not None and reach.can_read(slug, path=row[0], preset=row[1]):
            kept.append(slug)
    return kept


def _lines(
    context: Context,
    documents: list[str],
    role: Role,
    extra_parts: Mapping[str, str] | None,
    seat: LiveSeat,
) -> dict[str, list[str]]:
    lines: dict[str, list[str]] = {part: [] for part in role.sees.parts}
    extras = extra_parts or {}
    for part in _EXTRA_ONLY:
        if part in lines and extras.get(part):
            lines[part] = [f"- {part}: {_clean(extras[part])}"]
    index = context.index
    blocked: dict[str, str] = {}
    if "blockers" in lines:
        blocked = structure.blocked(recipes.item_rows(index, context.pack))
    try:
        reach = resolve_reach(index, context.pack, role, seat)
    except ReachError:
        reach = None
    for slug in documents:
        row = index.conn.execute(
            "SELECT title, status, category, preset FROM docs WHERE slug = ? LIMIT 1",
            (slug,),
        ).fetchone()
        if row is None:
            continue
        title, status, category, preset = row
        for part in role.sees.parts:
            if part in _EXTRA_ONLY:
                continue
            line = _line(
                context,
                part,
                slug,
                title or slug,
                status,
                category,
                preset,
                blocked,
                reach,
            )
            if line:
                lines[part].append(line)
    return lines


def _line(
    context: Context,
    part: str,
    slug: str,
    title: str,
    status: str | None,
    category: str | None,
    preset: str | None,
    blocked: Mapping[str, str],
    reach: Any,
) -> str | None:
    if part == "title":
        return f"- {slug}: {title}"
    if part == "status":
        return f"- {slug}: {status or 'none'} ({category or 'unknown'})"
    if part == "next":
        declared = context.pack.presets.get(preset or "")
        if declared is None:
            return None
        states = sorted(context.pack.workflow_for(declared.name).transitions.get(status or "", ()))
        return f"- {slug}: {', '.join(states) or 'none'}"
    if part == "body":
        try:
            shown = query.show(context.index, slug, body_chars=_BODY_CHARS)
        except LookupError:
            return None
        body = shown.get("body") or ""
        return f"- {slug}: {_clean(body)[:_BODY_CHARS]}" if body.strip() else None
    if part == "acceptance":
        values = _field(context, slug, "acceptance")
        return f"- {slug}: {values}" if values else None
    if part == "blockers":
        reason = blocked.get(slug)
        if reason and reason.startswith("depends_on:") and reach is not None:
            dependency = reason.partition(":")[2]
            row = context.index.conn.execute(
                "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1",
                (dependency,),
            ).fetchone()
            if row is None or not reach.can_read(
                dependency, path=row[0], preset=row[1]
            ):
                reason = "depends_on"
        return f"- {slug}: {reason}" if reason else None
    if part == "waits":
        values = _field(context, slug, "waits")
        return f"- {slug}: {values}" if values else None
    if part == "candidates":
        rows = query.candidates_for(context.index, slug, limit=_CANDIDATES)
        return (
            "- "
            + slug
            + ": "
            + "; ".join(
                f"attempt {row['attempt']} {row['commit'][:12]} by {row['actor']}"
                for row in rows
            )
            if rows
            else None
        )
    if part == "records":
        rows = query.records_about(context.index, slug, limit=_RECORDS)
        return (
            "- "
            + slug
            + ": "
            + ", ".join(str(row.get("kind")) for row in rows)
            if rows
            else None
        )
    return None


def _field(context: Context, slug: str, key: str) -> str | None:
    row = context.index.conn.execute(
        "SELECT d.path FROM docs d WHERE d.slug = ? LIMIT 1", (slug,)
    ).fetchone()
    if row is None:
        return None
    value = context.index.conn.execute(
        "SELECT value_json FROM fields WHERE path = ? AND key = ?", (row[0], key)
    ).fetchone()
    if value is None:
        return None
    try:
        decoded = json.loads(value[0])
    except (TypeError, ValueError):
        return None
    if isinstance(decoded, list):
        return ", ".join(_clean(str(item)) for item in decoded) or None
    return _clean(str(decoded)) or None


def _fit_heads(
    heads: list[str],
    parts: list[str],
    lines: Mapping[str, list[str]],
    budget: int,
) -> tuple[str, dict[str, int], list[str], list[str]]:
    last_result: tuple[str, dict[str, int], list[str], list[str], bool] | None = None
    for head in heads:
        result = _fit(head, parts, lines, budget)
        last_result = result
        if result[4]:
            return result[:4]
    assert last_result is not None
    return last_result[:4]


def _fit(
    header: str,
    parts: list[str],
    lines: Mapping[str, list[str]],
    budget: int,
) -> tuple[str, dict[str, int], list[str], list[str], bool]:
    """Trim the last part's entries, then drop whole parts, until the text fits."""
    fitted = list(parts)
    trimmed: dict[str, int] = {}
    dropped: list[str] = []

    def omitted() -> str:
        bits = [f"{part} (dropped)" for part in dropped]
        bits.extend(
            f"{part} ({count} trimmed)" for part, count in trimmed.items() if count
        )
        return "omitted: " + ", ".join(bits) if bits else ""

    def render() -> str:
        chunks: list[str] = []
        for part in fitted:
            keep = lines[part][: len(lines[part]) - trimmed.get(part, 0)]
            if keep:
                chunks.append("\n".join(keep))
        if not chunks:
            tail = omitted()
            return header + ("\n\n" + tail if tail else "")
        chunks.insert(0, header)
        body = "\n".join(chunks)
        tail = omitted()
        return body + ("\n\n" + tail if tail else "")

    while True:
        text = render()
        if len(text) <= budget:
            return text, trimmed, dropped, fitted, True
        if not fitted:
            return text[:budget], trimmed, dropped, fitted, False
        last = fitted[-1]
        removed = trimmed.get(last, 0)
        if removed < len(lines[last]):
            trimmed[last] = removed + 1
            continue
        fitted.pop()
        dropped.append(last)


def _clean(text: str) -> str:
    return " ".join(text.split())
