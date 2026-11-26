"""The project map: the work tree every reader shares, shaped by the pack.

The map is a projection, never a second truth. Items hang under their
``part_of`` parent; notes, checks and titled records attach to the item they
point at; markers come from the operating resolvers. Reads are bounded and go
through the same reach-filtered row selection as focus, queue and progress, so
a seat never sees a node it may not read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from context_rail.context import operating
from context_rail.context.operating import ItemRow, OperatingError
from context_rail.kernel.timestamps import canonical_iso
from context_rail.model.primitives import FINISHED_CATEGORIES

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.model.compiler import ProjectModel

__all__ = [
    "DEPTH_BOUNDS",
    "MAP_CHARS",
    "MAP_CHARS_BOUNDS",
    "MapTree",
    "build_tree",
    "resolve_map",
    "tree_payload",
]

MAP_CHARS = 6000
MAP_CHARS_BOUNDS = (200, 32000)
DEPTH_BOUNDS = (0, 12)
_WHOLE_DEPTH = 2
_CENTER_DEPTH = 1
_NEXT_CAP = 5
_SIBLING_CAP = 12
_LIST_CAP = 40
_SEP = " > "


@dataclass
class MapTree:
    """Every readable item placed on the spine, with its markers and rollup."""

    rows: dict[str, ItemRow]
    parent: dict[str, str]
    also: dict[str, tuple[str, ...]]
    children: dict[str, list[str]]
    roots: list[str]
    order: dict[str, int]
    rollup: dict[str, tuple[int, int]]
    below: dict[str, int]
    attachments: dict[str, int]
    blocked: set[str]
    changed: set[str]
    focus: str | None
    focus_state: str
    focus_candidates: list[dict[str, Any]]
    path: list[str]
    next: list[str]
    progress: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)

    def depth(self, slug: str) -> int:
        depth = 0
        seen = {slug}
        current = self.parent.get(slug)
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            current = self.parent.get(current)
        return depth

    def ancestors(self, slug: str) -> list[str]:
        """The primary chain from the root down to, not including, ``slug``."""
        return _chain(self.parent, slug)

    def markers(self, slug: str) -> list[str]:
        found: list[str] = []
        if slug == self.focus:
            found.append("focus")
        elif slug in self._path_set:
            found.append("path")
        if slug in self._next_set:
            found.append("next")
        if slug in self.blocked:
            found.append("blocked")
        if slug in self.changed:
            found.append("changed")
        return found

    def node(self, slug: str) -> dict[str, Any]:
        row = self.rows[slug]
        rollup = self.rollup.get(slug)
        return {
            "slug": slug,
            "title": row.title,
            "preset": row.preset,
            "label": self.labels.get(row.preset, row.preset),
            "status": row.status,
            "category": row.category,
            "parent": self.parent.get(slug),
            "also": list(self.also.get(slug, ())),
            "depth": self.depth(slug),
            "children": len(self.children.get(slug, ())),
            "below": self.below.get(slug, 0),
            "rollup": None
            if rollup is None
            else {"complete": rollup[0], "total": rollup[1]},
            "markers": self.markers(slug),
            "attachments": self.attachments.get(slug, 0),
            "updated": row.updated,
        }

    def __post_init__(self) -> None:
        self._path_set = set(self.path)
        self._next_set = set(self.next)


def build_tree(
    ctx: Context, model: ProjectModel, actor: str, *, since: str | None = None
) -> MapTree:
    """Place every readable item on the spine and compute its markers."""
    stamp = _since(since)
    rows = {row.slug: row for row in operating._rows(ctx, None, with_seq=True)}
    all_parents = operating._parents(ctx)
    parent: dict[str, str] = {}
    also: dict[str, tuple[str, ...]] = {}
    for slug in rows:
        readable = [dst for dst in all_parents.get(slug, ()) if dst in rows and dst != slug]
        if readable:
            parent[slug] = readable[0]
            if len(readable) > 1:
                also[slug] = tuple(readable[1:])
    _break_cycles(parent)
    hierarchy = [level.preset for level in model.operating.hierarchy]
    level_rank = {preset: rank for rank, preset in enumerate(hierarchy)}
    containers = {
        name for name, preset in ctx.pack.presets.items() if preset.container
    } | set(hierarchy)

    def sort_key(slug: str) -> tuple[Any, ...]:
        row = rows[slug]
        order = operating._group_order_field(model, row.preset)
        return operating._order_key(row, order)

    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for slug in rows:
        owner = parent.get(slug)
        if owner is None:
            roots.append(slug)
        else:
            children.setdefault(owner, []).append(slug)
    for bucket in children.values():
        bucket.sort(key=sort_key)
    roots.sort(
        key=lambda slug: (
            0 if rows[slug].preset in containers or slug in children else 1,
            level_rank.get(rows[slug].preset, len(level_rank)),
            sort_key(slug),
        )
    )
    order: dict[str, int] = {}
    stack = list(reversed(roots))
    while stack:
        slug = stack.pop()
        if slug in order:
            continue
        order[slug] = len(order)
        stack.extend(reversed(children.get(slug, [])))

    counted = set(model.operating.progress.of)
    done_cache: dict[tuple[str, str], bool] = {}

    def complete(row: ItemRow) -> bool:
        key = (row.preset, row.status)
        cached = done_cache.get(key)
        if cached is None:
            cached = operating._is_complete(ctx, model, row)
            done_cache[key] = cached
        return cached

    rollup: dict[str, tuple[int, int]] = {}
    below: dict[str, int] = {}
    for slug in sorted(order, key=order.__getitem__, reverse=True):
        done = total = size = 0
        for child in children.get(slug, ()):
            size += 1 + below.get(child, 0)
            child_done, child_total = rollup.get(child, (0, 0))
            done += child_done
            total += child_total
            row = rows[child]
            if row.preset in counted:
                total += 1
                done += 1 if complete(row) else 0
        below[slug] = size
        if total:
            rollup[slug] = (done, total)

    focus_slug: str | None = None
    focus_state = "none"
    candidates: list[dict[str, Any]] = []
    try:
        resolved = operating.resolve_focus(ctx, model)
        if resolved.focus is not None and resolved.focus.slug in rows:
            focus_slug = resolved.focus.slug
            focus_state = "resolved"
        candidates = [row.as_ref() for row in resolved.candidates]
    except OperatingError as error:
        focus_state = "ambiguous" if error.code == "AMBIGUOUS_FOCUS" else "invalid"
        candidates = list(error.details.get("candidates", []))
    next_slugs: list[str] = []
    try:
        queue = operating.resolve_queue(ctx, model, actor)
        next_slugs = [item.slug for item in queue.items if item.slug in rows]
    except OperatingError:
        next_slugs = []

    return MapTree(
        rows=rows,
        parent=parent,
        also=also,
        children=children,
        roots=roots,
        order=order,
        rollup=rollup,
        below=below,
        attachments=_attachment_counts(ctx, rows),
        blocked=_blocked(ctx, rows),
        changed={
            slug for slug, row in rows.items() if stamp is not None and (row.updated or row.created) >= stamp
        },
        focus=focus_slug,
        focus_state=focus_state,
        focus_candidates=candidates,
        path=[] if focus_slug is None else _chain(parent, focus_slug),
        next=next_slugs[:_NEXT_CAP],
        progress=_progress(ctx, model),
        labels={name: operating.label(model, name) for name in ctx.pack.presets},
    )


def resolve_map(
    ctx: Context,
    model: ProjectModel,
    actor: str,
    *,
    center: str | None = None,
    depth: int | None = None,
    find: str | None = None,
    since: str | None = None,
    max_chars: int = MAP_CHARS,
) -> dict[str, Any]:
    """The bounded map read: whole picture, one branch, a search or what moved."""
    chars = _bounded(max_chars, MAP_CHARS_BOUNDS, "max_chars")
    if depth is not None:
        depth = _bounded(depth, DEPTH_BOUNDS, "depth")
    words: list[str] | None = None
    if find is not None:
        words = [word for word in str(find).lower().split() if word]
        if not words:
            raise OperatingError(
                "VALIDATION_FAILED", "find needs at least one word", {"find": find}
            )
    tree = build_tree(ctx, model, actor, since=since)
    center_slug: str | None = None
    if center is not None:
        center_slug = str(center).strip()
        if center_slug not in tree.rows:
            _refuse_center(ctx, center_slug)

    header = _header(tree)
    blocks: list[list[str]] = []
    payload: dict[str, Any] = {
        "where": _where(tree),
        "center": None,
        "breadcrumb": [],
        "attachments": [],
        "dependencies": {"needs": [], "needed_by": []},
        "siblings": [],
        "matches": [],
        "changed": [],
    }
    if center_slug is not None:
        payload["center"] = {**tree.node(center_slug), "etag": tree.rows[center_slug].etag}
        payload["breadcrumb"] = [_ref(tree, slug) for slug in tree.ancestors(center_slug)]
        payload["attachments"] = _attachments(ctx, center_slug)
        payload["dependencies"] = _dependencies(ctx, tree, center_slug)
        payload["siblings"] = _siblings(tree, center_slug)
        blocks.append(_center_lines(tree, center_slug, payload))
    if words is not None:
        scope = None if center_slug is None else _subtree(tree, center_slug)
        matches = [
            slug
            for slug in sorted(tree.rows, key=tree.order.__getitem__)
            if (scope is None or slug in scope)
            and all(word in f"{tree.rows[slug].title} {slug}".lower() for word in words)
        ]
        payload["matches"] = [
            {**tree.node(slug), "path": _trail(tree, slug)} for slug in matches[:_LIST_CAP]
        ]
        blocks.append(_listing(tree, f"Found {len(matches)} for {find!r}", matches))
    if since is not None:
        moved = sorted(tree.changed, key=tree.order.__getitem__)
        payload["changed"] = [
            {**tree.node(slug), "path": _trail(tree, slug)} for slug in moved[:_LIST_CAP]
        ]
        blocks.append(_listing(tree, f"Changed since {_since(since)}: {len(moved)}", moved))
    if words is None:
        start = depth if depth is not None else (
            _CENTER_DEPTH if center_slug is not None else _WHOLE_DEPTH
        )
        text, shown, omitted = _fit_tree(tree, header, blocks, center_slug, start, chars)
    else:
        text = _clip(_join(header, blocks, []), chars)
        shown, omitted = [], 0
    payload["nodes"] = [tree.node(slug) for slug in shown]
    payload["omitted"] = omitted
    payload["text"] = text
    payload["estimated_tokens"] = math.ceil(len(text) / 4)
    payload["total"] = len(tree.rows)
    return payload


def tree_payload(tree: MapTree) -> dict[str, Any]:
    """The whole tree for a screen that folds and unfolds it itself."""
    return {
        "where": _where(tree),
        "roots": list(tree.roots),
        "nodes": [tree.node(slug) for slug in sorted(tree.rows, key=tree.order.__getitem__)],
        "total": len(tree.rows),
    }


# ── tree selection and rendering ─────────────────────────────────────────


def _fit_tree(
    tree: MapTree,
    header: list[str],
    blocks: list[list[str]],
    center: str | None,
    depth: int,
    chars: int,
) -> tuple[str, list[str], int]:
    """Render the deepest tree that fits, then cut from the bottom if needed."""
    pinned = set(tree.path)
    if tree.focus is not None:
        pinned.add(tree.focus)
    heading = "## Tree" if center is None else "## Subtree"
    scope = len(_scope(tree, center))
    for level in range(depth, -1, -1):
        shown = _select(tree, center, level, pinned)
        lines = [_tree_line(tree, slug, shown, center) for slug in shown]
        text = _join(header, blocks, lines, heading)
        if len(text) <= chars:
            return text, shown, scope - len(shown)
    shown = _select(tree, center, 0, pinned)
    lines = [_tree_line(tree, slug, shown, center) for slug in shown]
    kept = len(lines)
    while kept > 0:
        kept -= 1
        tail = (
            f"... {len(shown) - kept} more node(s): narrow with read map <slug> "
            "or raise --max-chars"
        )
        text = _join(header, blocks, [*lines[:kept], tail], heading)
        if len(text) <= chars:
            return text, shown[:kept], scope - kept
    return _clip(_join(header, blocks, []), chars), [], scope


def _select(
    tree: MapTree, center: str | None, depth: int, pinned: set[str]
) -> list[str]:
    """Nodes to draw: everything to ``depth``, the focus path and its children."""
    starts = tree.roots if center is None else [center]
    open_nodes = set(pinned)
    if tree.focus is not None and center is None:
        open_nodes.update(tree.children.get(tree.focus, ()))
    chosen: list[str] = []
    stack = [(slug, 0) for slug in reversed(starts)]
    while stack:
        slug, level = stack.pop()
        chosen.append(slug)
        kids = tree.children.get(slug, ())
        if level < depth:
            stack.extend((child, level + 1) for child in reversed(kids))
        elif center is None:
            stack.extend(
                (child, level + 1) for child in reversed(kids) if child in open_nodes
            )
    return chosen


def _scope(tree: MapTree, center: str | None) -> set[str]:
    return set(tree.rows) if center is None else _subtree(tree, center)


def _subtree(tree: MapTree, slug: str) -> set[str]:
    found: set[str] = set()
    stack = [slug]
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(tree.children.get(current, ()))
    return found


def _tree_line(
    tree: MapTree, slug: str, shown: list[str], center: str | None
) -> str:
    base = 0 if center is None else tree.depth(center)
    indent = "  " * (tree.depth(slug) - base)
    hidden = sum(1 for child in tree.children.get(slug, ()) if child not in shown)
    return indent + "- " + _describe(tree, slug, hidden=hidden)


def _describe(tree: MapTree, slug: str, *, hidden: int = 0) -> str:
    row = tree.rows[slug]
    label = tree.labels.get(row.preset, row.preset)
    parts = [f"{slug} \"{row.title}\" ({label}, {row.status})"]
    rollup = tree.rollup.get(slug)
    if rollup is not None:
        parts.append(f"{rollup[0]}/{rollup[1]} done")
    parts.extend(marker.upper() for marker in tree.markers(slug))
    count = tree.attachments.get(slug, 0)
    if count:
        parts.append(f"{count} attached")
    also = tree.also.get(slug)
    if also:
        parts.append("also under " + ", ".join(also))
    if hidden:
        below = sum(
            1 + tree.below.get(child, 0)
            for child in tree.children.get(slug, ())
        )
        parts.append(f"+{below} below")
    return " | ".join(parts)


def _header(tree: MapTree) -> list[str]:
    lines = ["# Project map"]
    if tree.focus is not None:
        trail = _SEP.join([*(tree.rows[slug].title for slug in tree.path), tree.rows[tree.focus].title])
        lines.append(f"Where we are: {trail} ({tree.focus})")
    elif tree.focus_state == "ambiguous":
        names = ", ".join(row.get("slug", "?") for row in tree.focus_candidates)
        lines.append(f"Where we are: focus is ambiguous between {names}")
    else:
        lines.append("Where we are: no focus container; the whole project is in scope")
    progress = tree.progress
    if progress.get("total"):
        lines.append(
            f"Progress: {progress['complete']}/{progress['total']} {progress['label']} done"
        )
    if tree.next:
        lines.append(
            "Next: " + "; ".join(f"{slug} \"{tree.rows[slug].title}\"" for slug in tree.next)
        )
    lines.append(
        f"{len(tree.rows)} item(s). Markers: FOCUS, PATH (leads to the focus), NEXT, "
        "BLOCKED, CHANGED. Go deeper with read map <slug>."
    )
    return lines


def _center_lines(tree: MapTree, slug: str, payload: dict[str, Any]) -> list[str]:
    lines = ["## Branch"]
    trail = _trail(tree, slug)
    lines.append(f"Path: {trail or '(root)'}")
    lines.append("Node: " + _describe(tree, slug))
    lines.append(f"Etag: {payload['center']['etag']} (pass it as --expect to change this node)")
    deps = payload["dependencies"]
    if deps["needs"]:
        lines.append(
            "Needs: " + "; ".join(f"{row['slug']} ({row['status']})" for row in deps["needs"])
        )
    if deps["needed_by"]:
        lines.append(
            "Needed by: "
            + "; ".join(f"{row['slug']} ({row['status']})" for row in deps["needed_by"])
        )
    for row in payload["attachments"]:
        kind = row.get("kind") or row["preset"] or row["primitive"]
        lines.append(f"Attached {kind}: {row['slug']} \"{row['title']}\" via {row['rel']}")
    if payload["siblings"]:
        lines.append(
            "Siblings: " + "; ".join(row["slug"] for row in payload["siblings"])
        )
    return lines


def _listing(tree: MapTree, title: str, slugs: list[str]) -> list[str]:
    lines = [f"## {title}"]
    for slug in slugs[:_LIST_CAP]:
        trail = _trail(tree, slug)
        where = f" | in {trail}" if trail else ""
        lines.append(f"- {_describe(tree, slug)}{where}")
    if len(slugs) > _LIST_CAP:
        lines.append(f"... {len(slugs) - _LIST_CAP} more")
    return lines


def _join(
    header: list[str],
    blocks: list[list[str]],
    tree_lines: list[str],
    heading: str = "## Tree",
) -> str:
    parts = ["\n".join(header)]
    parts.extend("\n".join(block) for block in blocks if block)
    if tree_lines:
        parts.append("\n".join([heading, *tree_lines]))
    return "\n\n".join(parts)


def _clip(text: str, chars: int) -> str:
    """Cut whole lines from the end so a budget never splits a line."""
    if len(text) <= chars:
        return text
    note = "... trimmed: raise --max-chars or read map <slug>"
    lines = text.split("\n")
    while lines and len("\n".join(lines)) + len(note) + 1 > chars:
        lines.pop()
    return "\n".join([*lines, note])[:chars]


def _trail(tree: MapTree, slug: str) -> str:
    return _SEP.join(tree.rows[ancestor].title for ancestor in tree.ancestors(slug))


def _ref(tree: MapTree, slug: str) -> dict[str, Any]:
    return tree.rows[slug].as_ref()


def _where(tree: MapTree) -> dict[str, Any]:
    return {
        "focus": None if tree.focus is None else _ref(tree, tree.focus),
        "focus_state": tree.focus_state,
        "candidates": list(tree.focus_candidates),
        "path": [_ref(tree, slug) for slug in tree.path],
        "next": [_ref(tree, slug) for slug in tree.next],
        "progress": dict(tree.progress),
        "roots": [
            {
                **_ref(tree, slug),
                "rollup": None
                if slug not in tree.rollup
                else {"complete": tree.rollup[slug][0], "total": tree.rollup[slug][1]},
            }
            for slug in tree.roots
            if tree.children.get(slug)
        ],
    }


def _siblings(tree: MapTree, slug: str) -> list[dict[str, Any]]:
    owner = tree.parent.get(slug)
    pool = tree.roots if owner is None else tree.children.get(owner, [])
    return [_ref(tree, other) for other in pool if other != slug][:_SIBLING_CAP]


# ── index reads ──────────────────────────────────────────────────────────


def _attachment_counts(ctx: Context, rows: dict[str, ItemRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    reach = ctx.reach
    for slug, path, preset, dst in ctx.index.conn.execute(
        "SELECT d.slug, d.path, d.preset, r.dst FROM relations r "
        "JOIN docs d ON d.path = r.src_path "
        "WHERE d.primitive != 'item' AND d.broken = 0 "
        "AND (d.primitive != 'record' OR COALESCE(d.title, '') != '')"
    ):
        if dst not in rows:
            continue
        if reach is not None and not reach.can_read(slug, path=path, preset=preset):
            continue
        counts[dst] = counts.get(dst, 0) + 1
    return counts


def _attachments(ctx: Context, slug: str) -> list[dict[str, Any]]:
    reach = ctx.reach
    found: list[dict[str, Any]] = []
    for doc, title, primitive, preset, path, created, rel, kind in ctx.index.conn.execute(
        "SELECT d.slug, d.title, d.primitive, d.preset, d.path, "
        "COALESCE(d.record_at, d.updated, d.created, ''), r.rel, "
        "(SELECT value_json FROM fields f WHERE f.path = d.path AND f.key = 'kind') "
        "FROM relations r JOIN docs d ON d.path = r.src_path "
        "WHERE r.dst = ? AND d.primitive != 'item' AND d.broken = 0 "
        "AND (d.primitive != 'record' OR COALESCE(d.title, '') != '')",
        (slug,),
    ):
        if reach is not None and not reach.can_read(doc, path=path, preset=preset):
            continue
        found.append(
            {
                "slug": doc,
                "title": title or doc,
                "primitive": primitive,
                "preset": preset,
                "kind": _json_text(kind),
                "rel": rel,
                "at": str(created or ""),
            }
        )
    found.sort(key=lambda row: (row["at"], row["slug"]), reverse=True)
    return found[:_LIST_CAP]


def _dependencies(ctx: Context, tree: MapTree, slug: str) -> dict[str, list[dict[str, Any]]]:
    needs = [
        _ref(tree, dst)
        for (dst,) in ctx.index.conn.execute(
            "SELECT dst FROM relations WHERE src = ? AND rel = 'depends_on' ORDER BY dst",
            (slug,),
        )
        if dst in tree.rows
    ]
    needed_by = [
        _ref(tree, src)
        for (src,) in ctx.index.conn.execute(
            "SELECT DISTINCT src FROM relations WHERE dst = ? AND rel = 'depends_on' "
            "ORDER BY src",
            (slug,),
        )
        if src in tree.rows
    ]
    return {"needs": needs, "needed_by": needed_by}


def _blocked(ctx: Context, rows: dict[str, ItemRow]) -> set[str]:
    held = {slug for slug, row in rows.items() if row.category == "blocked"}
    categories = dict(
        ctx.index.conn.execute(
            "SELECT slug, category FROM docs WHERE primitive = 'item' AND broken = 0"
        ).fetchall()
    )
    for src, dst in ctx.index.conn.execute(
        "SELECT src, dst FROM relations WHERE rel = 'depends_on'"
    ):
        row = rows.get(src)
        if row is None or row.category in FINISHED_CATEGORIES:
            continue
        if categories.get(dst) not in FINISHED_CATEGORIES:
            held.add(src)
    return held


def _progress(ctx: Context, model: ProjectModel) -> dict[str, Any]:
    view = operating.resolve_progress(ctx, model)
    of = list(view.of)
    unit = model.operating.unit
    if unit is not None:
        noun = operating.label(model, unit, plural=True)
    elif len(of) == 1:
        noun = operating.label(model, of[0], plural=True)
    else:
        noun = "items"
    return {"complete": view.complete, "total": view.total, "label": noun.lower(), "of": of}


def _refuse_center(ctx: Context, slug: str) -> None:
    row = ctx.index.conn.execute(
        "SELECT primitive FROM docs WHERE slug = ? AND broken = 0 LIMIT 1", (slug,)
    ).fetchone()
    if row is None:
        raise OperatingError("NOT_FOUND", f"no item {slug!r} on the map", {"item": slug})
    if row[0] != "item":
        raise OperatingError(
            "VALIDATION_FAILED",
            f"{slug!r} is a {row[0]}; the map centers on items",
            {"item": slug, "primitive": row[0]},
        )
    raise OperatingError(
        "PERMISSION_DENIED",
        f"{slug!r} is outside the seat's read reach",
        {"item": slug, "reason": "out_of_reach"},
    )


def _chain(parent: dict[str, str], slug: str) -> list[str]:
    chain: list[str] = []
    seen = {slug}
    current = parent.get(slug)
    while current is not None and current not in seen:
        seen.add(current)
        chain.append(current)
        current = parent.get(current)
    chain.reverse()
    return chain


def _break_cycles(parent: dict[str, str]) -> None:
    """Drop the edge that closes a ``part_of`` loop an old store may still hold."""
    for start in list(parent):
        seen: set[str] = set()
        current: str | None = start
        while current is not None and current in parent:
            if current in seen:
                parent.pop(current, None)
                break
            seen.add(current)
            current = parent.get(current)


def _since(value: str | None) -> str | None:
    if value is None:
        return None
    stamp = canonical_iso(value) if isinstance(value, str) and value.strip() else None
    if stamp is None:
        raise OperatingError(
            "VALIDATION_FAILED", f"since is not a UTC timestamp: {value!r}", {"since": value}
        )
    return stamp


def _bounded(value: Any, bounds: tuple[int, int], name: str) -> int:
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise OperatingError(
            "VALIDATION_FAILED",
            f"{name} must be an integer from {low} to {high}",
            {name: value},
        )
    return value


def _json_text(raw: Any) -> str | None:
    if raw is None:
        return None
    import json

    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, str) else None
