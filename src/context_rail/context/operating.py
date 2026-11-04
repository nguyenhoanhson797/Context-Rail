"""Runtime resolvers over the compiled operating model and the index.

Focus, unit, hierarchy, queue and progress are read-only. They never execute a
command, hash a source tree or write; each call reads the index and returns an
immutable view. No command implements these rules independently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from context_rail.kernel import rights

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.model.compiler import ProjectModel

__all__ = [
    "FocusResolution",
    "Group",
    "ItemRow",
    "OperatingError",
    "ProgressView",
    "QueueItem",
    "QueueView",
    "Unit",
    "label",
    "resolve_focus",
    "resolve_hierarchy",
    "resolve_progress",
    "resolve_queue",
    "resolve_unit",
]

_STATUS_RANK = {"active": 0, "blocked": 1, "not_started": 2, "done": 3, "dropped": 4}


class OperatingError(Exception):
    """A runtime operating refusal: no focus, an ambiguous focus or a bad one."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ItemRow:
    """One indexed work item with the fields a resolver compares."""

    slug: str
    title: str
    preset: str
    workflow: str
    status: str
    category: str
    created: str
    path: str
    etag: str
    updated: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def as_ref(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "preset": self.preset,
            "status": self.status,
            "category": self.category,
        }


@dataclass(frozen=True)
class FocusResolution:
    """The focus state one operation sees, with its candidates when unresolved."""

    state: str
    focus: ItemRow | None
    candidates: tuple[ItemRow, ...]
    resolved_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "focus": None if self.focus is None else self.focus.as_ref(),
            "candidates": [row.as_ref() for row in self.candidates],
            "resolved_by": self.resolved_by,
        }


@dataclass(frozen=True)
class Unit:
    """The pack's canonical work item and the workflow its board uses."""

    preset: str
    workflow: str
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"preset": self.preset, "workflow": self.workflow, "label": self.label}


@dataclass(frozen=True)
class Group:
    """One hierarchy level and the containers it orders."""

    preset: str
    label: str
    order: str | None
    items: tuple[ItemRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "label": self.label,
            "order": self.order,
            "items": [row.as_ref() for row in self.items],
        }


@dataclass(frozen=True)
class QueueItem:
    """One eligible work item, its applicable actions and why it is here."""

    slug: str
    title: str
    focus: bool
    actions: tuple[str, ...]
    why: str
    status: str
    preset: str
    workflow: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "focus": self.focus,
            "actions": list(self.actions),
            "why": self.why,
            "status": self.status,
            "preset": self.preset,
            "workflow": self.workflow,
        }


@dataclass(frozen=True)
class QueueView:
    """The queue, its scope and what it deliberately left out."""

    items: tuple[QueueItem, ...]
    omitted: int
    focus: FocusResolution
    scope: str
    candidates: tuple[ItemRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "omitted": self.omitted,
            "focus": self.focus.as_dict(),
            "scope": self.scope,
            "candidates": [row.as_ref() for row in self.candidates],
        }


@dataclass(frozen=True)
class ProgressView:
    """Workflow progress over the declared population, never execution runs."""

    group: str | None
    total: int
    complete: int
    by_category: dict[str, int]
    children: tuple[dict[str, Any], ...]
    omitted: int
    runs: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "total": self.total,
            "complete": self.complete,
            "by_category": dict(self.by_category),
            "children": [dict(child) for child in self.children],
            "omitted": self.omitted,
            "runs": None if self.runs is None else dict(self.runs),
        }


def label(model: ProjectModel, preset: str, *, plural: bool = False) -> str:
    """The presentation's label for a preset, falling back to its name."""
    declared = model.presentation.labels.get(preset)
    if declared is None:
        return preset.replace("-", " ").title()
    return declared.many if plural else declared.one


def item_row(ctx: Context, slug: str) -> ItemRow | None:
    """One indexed item in the resolver's own shape, or None."""
    return _one(ctx, slug)


def role_for(model: ProjectModel, preset: str) -> str:
    """How a preset participates in the operating model."""
    if preset == model.operating.unit:
        return "unit"
    if preset in {level.preset for level in model.operating.hierarchy}:
        return "container"
    if preset in model.operating.queue.presets:
        return "queue"
    if preset in model.operating.progress.of:
        return "progress"
    return "other"


def focus_membership(
    ctx: Context, model: ProjectModel, slug: str
) -> dict[str, Any] | None:
    """The focus container one item belongs to, including itself."""
    try:
        resolved = resolve_focus(ctx, model)
    except OperatingError:
        return None
    if resolved.focus is None:
        return None
    if resolved.focus.slug == slug:
        return resolved.focus.as_ref()
    if resolved.focus.slug in _ancestors(slug, _parents(ctx)):
        return resolved.focus.as_ref()
    return None


def outside_focus(ctx: Context, model: ProjectModel, slug: str) -> str | None:
    """The focus slug when ``slug`` is outside it, else None.

    Raises :class:`OperatingError` when the focus is ambiguous, so a caller
    never starts work under a guess. No focus policy or ``resolve: none`` means
    the project is project-wide and everything is inside it.
    """
    resolved = resolve_focus(ctx, model)
    if resolved.focus is None:
        return None
    if resolved.focus.slug == slug:
        return None
    if resolved.focus.slug in _ancestors(slug, _parents(ctx)):
        return None
    return resolved.focus.slug


def resolve_unit(ctx: Context, model: ProjectModel) -> Unit | None:
    """The canonical work item, or None for a heterogeneous pack."""
    name = model.operating.unit
    if name is None:
        return None
    preset = ctx.pack.presets.get(name)
    if preset is None:
        return None
    return Unit(
        preset=name,
        workflow=preset.workflow,
        label=label(model, name),
    )


def resolve_focus(
    ctx: Context, model: ProjectModel, *, into: str | None = None
) -> FocusResolution:
    """Resolve the focus, refusing an ambiguity instead of picking one."""
    policy = model.operating.focus
    hierarchy = {level.preset for level in model.operating.hierarchy}
    if into is not None and str(into).strip():
        row = _one(ctx, str(into).strip())
        if row is None:
            raise OperatingError(
                "INVALID_FOCUS",
                f"no item {into!r} to use as the focus",
                {"into": into},
            )
        if row.preset not in hierarchy:
            raise OperatingError(
                "INVALID_FOCUS",
                f"{row.slug!r} is a {row.preset}, not a hierarchy container",
                {"into": row.slug, "preset": row.preset},
            )
        if policy is not None and row.preset != policy.preset:
            raise OperatingError(
                "INVALID_FOCUS",
                f"{row.slug!r} is a {row.preset}; the focus is a {policy.preset}",
                {"into": row.slug, "expected": policy.preset},
            )
        return FocusResolution("resolved", row, (), "into")
    if policy is None or policy.resolve == "none":
        return FocusResolution("none", None, (), "none")
    candidates = _candidates(ctx, model)
    if policy.resolve == "explicit":
        return FocusResolution("none", None, candidates, "explicit")
    if not candidates:
        return FocusResolution("none", None, (), "unique")
    if len(candidates) == 1:
        return FocusResolution("resolved", candidates[0], (), "unique")
    if policy.resolve == "first":
        return FocusResolution("resolved", candidates[0], candidates, "first")
    raise OperatingError(
        "AMBIGUOUS_FOCUS",
        f"{len(candidates)} {policy.preset} containers are {policy.category}; "
        "pass --into to choose one",
        {
            "preset": policy.preset,
            "category": policy.category,
            "candidates": [row.as_ref() for row in candidates],
        },
    )


def require_focus(
    ctx: Context, model: ProjectModel, *, into: str | None = None
) -> ItemRow:
    """The focus one write needs, refusing when the project has none."""
    resolved = resolve_focus(ctx, model, into=into)
    if resolved.focus is None:
        raise OperatingError(
            "NO_FOCUS",
            "no focus container matches; pass --into or start one first",
            {
                "preset": None
                if model.operating.focus is None
                else model.operating.focus.preset,
                "candidates": [row.as_ref() for row in resolved.candidates],
            },
        )
    return resolved.focus


def resolve_hierarchy(ctx: Context, model: ProjectModel) -> tuple[Group, ...]:
    """Every declared hierarchy level, its containers and their order key."""
    groups: list[Group] = []
    for level in model.operating.hierarchy:
        rows = _rows(ctx, (level.preset,))
        rows.sort(key=lambda row: _order_key(row, level.order))
        groups.append(
            Group(
                preset=level.preset,
                label=label(model, level.preset, plural=True),
                order=level.order,
                items=tuple(rows),
            )
        )
    return tuple(groups)


def resolve_queue(
    ctx: Context,
    model: ProjectModel,
    actor: str,
    *,
    all: bool = False,
    into: str | None = None,
    focus_only: bool = False,
) -> QueueView:
    """The operating queue: presets, eligibility, focus priority, stable order."""
    focus = resolve_focus(ctx, model, into=into)
    focus_slug = None if focus.focus is None else focus.focus.slug
    presets = model.operating.queue.presets
    if all:
        presets = tuple(
            preset
            for preset in sorted(ctx.pack.presets)
            if ctx.pack.presets[preset].primitive == "item"
        )
    rows = _rows(ctx, presets)
    parents = _parents(ctx)
    grouped = _group_order(ctx, model)
    by_slug = {row.slug: row for row in rows}
    scope = "all" if all else ("focus" if focus_slug else "project")
    if focus_only:
        rows = (
            []
            if focus_slug is None
            else [
                row
                for row in rows
                if focus_slug in _ancestors(row.slug, parents)
                or row.slug == focus_slug
            ]
        )
    eligible: list[QueueItem] = []
    omitted = 0
    for row in rows:
        if row.category in ("done", "dropped"):
            continue
        actions = applicable_actions(ctx, model, row, actor)
        if model.operating.queue.require == "action":
            if not actions:
                omitted += 1
                continue
        elif not ctx.pack.workflow_for(row.preset).transitions.get(row.status, ()):
            omitted += 1
            continue
        in_focus = focus_slug is not None and (
            row.slug == focus_slug or focus_slug in _ancestors(row.slug, parents)
        )
        eligible.append(
            QueueItem(
                slug=row.slug,
                title=row.title,
                focus=in_focus,
                actions=actions,
                why="focus" if in_focus else "preset",
                status=row.status,
                preset=row.preset,
                workflow=row.workflow,
            )
        )
    eligible.sort(
        key=lambda item: _queue_key(
            item, focus_slug, parents, grouped, by_slug
        )
    )
    return QueueView(
        items=tuple(eligible),
        omitted=omitted,
        focus=focus,
        scope=scope,
        candidates=focus.candidates,
    )


def applicable_actions(
    ctx: Context, model: ProjectModel, row: ItemRow, actor: str
) -> tuple[str, ...]:
    """Every declared action that applies to this row's preset, state and actor."""
    from context_rail.core.tool_names import canonical_methods
    from context_rail.gateway.tools import TOOLS

    specs = {tool.name: tool for tool in TOOLS}
    profile = getattr(ctx, "profile", None)
    methods = canonical_methods(profile.allowed_methods) if profile is not None else None
    names: list[str] = []
    for action in ctx.pack.actions.values():
        if action.on != "*" and action.on != row.preset:
            continue
        if action.from_states and row.status not in action.from_states:
            continue
        if methods is not None and action.tool not in methods:
            continue
        spec = specs.get(action.tool)
        if spec is not None and rights.allowed(spec.permission, actor) is not None:
            continue
        if not _requires_met(ctx, action, row.slug):
            continue
        names.append(action.name)
    return tuple(sorted(names))


def resolve_progress(
    ctx: Context, model: ProjectModel, group: str | None = None
) -> ProgressView:
    """Workflow progress over ``progress.of``, grouped along the hierarchy."""
    policy = model.operating.progress
    rows = _rows(ctx, policy.of)
    counts: dict[str, int] = {}
    complete = 0
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1
        if _is_complete(ctx, model, row):
            complete += 1
    group_preset = group or policy.group
    children: list[dict[str, Any]] = []
    if group_preset is not None:
        containers = _rows(ctx, (group_preset,))
        containers.sort(key=lambda row: _order_key(row, _group_order_field(model, group_preset)))
        parents = _parents(ctx)
        members: dict[str, list[ItemRow]] = {row.slug: [] for row in containers}
        for row in rows:
            parent = _nearest_ancestor(row.slug, parents, members)
            if parent is not None:
                members[parent].append(row)
        for container in containers:
            bucket = members[container.slug]
            children.append(
                {
                    "slug": container.slug,
                    "title": container.title,
                    "total": len(bucket),
                    "complete": sum(
                        1 for row in bucket if _is_complete(ctx, model, row)
                    ),
                }
            )
    return ProgressView(
        group=group_preset,
        total=len(rows),
        complete=complete,
        by_category=counts,
        children=tuple(children),
        omitted=0,
        runs=_execution_summary(ctx, model, rows),
    )


_RUN_CAP = 20


def _execution_summary(
    ctx: Context, model: ProjectModel, rows: list[ItemRow]
) -> dict[str, Any] | None:
    """The latest declared execution state per target, or None when no run exists."""
    actions = model.operating.execution_actions
    if not actions or not rows:
        return None
    from context_rail.store import query

    latest = query.execution_latest(
        ctx.index, actions=actions, targets=tuple(row.slug for row in rows)
    )
    if not latest:
        return None
    by_state = {state: 0 for state in ("running", "succeeded", "failed", "cancelled")}
    for row in latest:
        state = row.get("state")
        if state in by_state:
            by_state[state] += 1
    return {
        "actions": list(actions),
        "total": len(latest),
        "by_state": by_state,
        "latest": latest[:_RUN_CAP],
        "omitted": max(len(latest) - _RUN_CAP, 0),
    }


def _is_complete(ctx: Context, model: ProjectModel, row: ItemRow) -> bool:
    rule = model.operating.progress.complete
    workflow = ctx.pack.workflow_for(row.preset)
    if row.preset in rule.presets:
        return row.status in rule.presets[row.preset]
    if rule.states:
        return row.status in rule.states and workflow.statuses.get(row.status) == "done"
    return workflow.statuses.get(row.status) == "done"


def _candidates(ctx: Context, model: ProjectModel) -> list[ItemRow]:
    policy = model.operating.focus
    if policy is None:
        return []
    rows = _rows(ctx, (policy.preset,))
    return sorted(
        (row for row in rows if row.category == policy.category),
        key=lambda row: _order_key(row, policy.order),
    )


def _group_order(ctx: Context, model: ProjectModel) -> dict[str, int]:
    order: dict[str, int] = {}
    position = 0
    for level in model.operating.hierarchy:
        for row in sorted(
            _rows(ctx, (level.preset,)), key=lambda item: _order_key(item, level.order)
        ):
            order[row.slug] = position
            position += 1
    return order


def _group_order_field(model: ProjectModel, preset: str) -> str | None:
    for level in model.operating.hierarchy:
        if level.preset == preset:
            return level.order
    return None


def _queue_key(
    item: QueueItem,
    focus_slug: str | None,
    parents: dict[str, tuple[str, ...]],
    grouped: dict[str, int],
    by_slug: dict[str, ItemRow],
) -> tuple[Any, ...]:
    row = by_slug.get(item.slug)
    parent_order = min(
        (
            grouped.get(ancestor, len(grouped))
            for ancestor in _ancestors(item.slug, parents)
        ),
        default=len(grouped),
    )
    return (
        0 if item.focus else 1,
        parent_order,
        _STATUS_RANK.get(item.status, 2),
        "" if row is None else (row.created or ""),
        item.slug,
    )


def _ancestors(slug: str, parents: dict[str, tuple[str, ...]]) -> list[str]:
    """Every ancestor, nearest first, with each slug visited once."""
    found: set[str] = set()
    ordered: list[str] = []
    queue = list(parents.get(slug, ()))
    while queue:
        current = queue.pop(0)
        if current in found:
            continue
        found.add(current)
        ordered.append(current)
        queue.extend(parents.get(current, ()))
    return ordered


def _nearest_ancestor(
    slug: str,
    parents: dict[str, tuple[str, ...]],
    containers: dict[str, list[ItemRow]],
) -> str | None:
    for candidate in _ancestors(slug, parents):
        if candidate in containers:
            return candidate
    return None


def _one(ctx: Context, slug: str) -> ItemRow | None:
    rows = _rows(ctx, None, slug=slug)
    return rows[0] if rows else None


def _rows(
    ctx: Context, presets: tuple[str, ...] | None, *, slug: str | None = None
) -> list[ItemRow]:
    index = ctx.index
    where = ["d.primitive = 'item'", "d.broken = 0"]
    params: list[Any] = []
    if presets is not None and not presets:
        return []
    if presets is not None:
        where.append(f"d.preset IN ({', '.join('?' * len(presets))})")
        params.extend(presets)
    if slug is not None:
        where.append("d.slug = ?")
        params.append(slug)
    rows = index.conn.execute(
        "SELECT d.slug, d.title, d.preset, d.workflow, d.status, d.category, "
        "d.created, d.updated, d.path, d.etag FROM docs d WHERE "
        + " AND ".join(where)
        + " ORDER BY d.slug",
        params,
    ).fetchall()
    if not rows:
        return []
    paths = [row[8] for row in rows]
    fields: dict[str, dict[str, Any]] = {path: {} for path in paths}
    for path, key, value in index.conn.execute(
        "SELECT path, key, value_json FROM fields WHERE path IN "
        f"({', '.join('?' * len(paths))})",
        paths,
    ):
        fields[path][key] = json.loads(value)
    return [
        ItemRow(
            slug=row[0],
            title=row[1] or row[0],
            preset=row[2] or "",
            workflow=row[3] or "default",
            status=row[4] or "",
            category=row[5] or "not_started",
            created=row[6] or "",
            updated=row[7] or "",
            path=row[8],
            etag=row[9],
            fields=fields.get(row[8], {}),
        )
        for row in rows
    ]


def _parents(ctx: Context) -> dict[str, tuple[str, ...]]:
    edges: dict[str, list[str]] = {}
    for src, dst in ctx.index.conn.execute(
        "SELECT src, dst FROM relations WHERE rel = 'part_of' ORDER BY dst"
    ):
        edges.setdefault(src, []).append(dst)
    return {src: tuple(sorted(values)) for src, values in edges.items()}


def _order_key(row: ItemRow, order: str | None) -> tuple[Any, ...]:
    if order == "title":
        raw: Any = row.title
    elif order == "slug":
        raw = row.slug
    elif order == "created":
        raw = row.created
    elif order == "updated":
        raw = row.updated
    elif order is None:
        raw = None
    else:
        raw = row.fields.get(order)
    return (_sortable(raw), row.created or "", row.slug)


def _sortable(value: Any) -> tuple[int, Any]:
    if isinstance(value, bool):
        return (1, 1)
    if isinstance(value, int):
        return (0, value)
    if isinstance(value, float):
        return (0, value)
    if isinstance(value, str) and value.strip():
        return (0, value)
    return (1, "")


def _requires_met(ctx: Context, action: Any, slug: str) -> bool:
    requires = action.requires
    if requires.children:
        rows = ctx.index.conn.execute(
            "SELECT d.category FROM relations r JOIN docs d ON d.path = r.src_path "
            "WHERE r.dst = ? AND r.rel = 'part_of'",
            (slug,),
        ).fetchall()
        if any(category not in ("done", "dropped") for (category,) in rows):
            return False
    if requires.check:
        from context_rail.store import query

        row = query.check_state(ctx.index, requires.check)
        if row is None or not row.get("passed"):
            return False
    if requires.review:
        from context_rail.store import query

        review = query.review_for_item(ctx.index, slug)
        if (review or {}).get("verdict") != "approve":
            return False
    if requires.field:
        found = ctx.index.conn.execute(
            "SELECT 1 FROM fields WHERE path IN "
            "(SELECT path FROM docs WHERE slug = ?) AND key = ? LIMIT 1",
            (slug, requires.field),
        ).fetchone()
        if found is None:
            return False
    return True


__all__ += [
    "applicable_actions",
    "focus_membership",
    "item_row",
    "outside_focus",
    "require_focus",
    "role_for",
]
