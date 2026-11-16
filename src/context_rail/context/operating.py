"""Runtime resolvers over the compiled operating model and the index.

Focus, unit, hierarchy, queue and progress are read-only. They never execute a
command, hash a source tree or write; each call reads the index and returns an
immutable view. No command implements these rules independently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from context_rail.kernel import rights, structure

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
    # Store write sequence (record ULID or index position): `created` only
    # carries seconds, so a burst of adds ties on it and the plan order needs
    # the order the store recorded even after a full reindex.
    seq: tuple[int, Any] = (1, 0)

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
    outside_reach: int = 0

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
    blocked: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "omitted": self.omitted,
            "focus": self.focus.as_dict(),
            "scope": self.scope,
            "candidates": [row.as_ref() for row in self.candidates],
            "blocked": dict(self.blocked),
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
    of: tuple[str, ...] = ()
    rule: Any | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "group": self.group,
            "total": self.total,
            "complete": self.complete,
            "by_category": dict(self.by_category),
            "children": [dict(child) for child in self.children],
            "omitted": self.omitted,
            "runs": None if self.runs is None else dict(self.runs),
        }
        payload["of"] = list(self.of)
        if self.rule is not None:
            payload["rule"] = {
                "states": list(getattr(self.rule, "states", ())),
                "category": getattr(self.rule, "category", None),
                "presets": {key: list(value) for key, value in getattr(self.rule, "presets", {}).items()},
            }
        return payload


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
    ctx: Context,
    model: ProjectModel,
    *,
    into: str | None = None,
    preset_rows: dict[str, list[ItemRow]] | None = None,
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
    candidates = _candidates(ctx, model, preset_rows=preset_rows)
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


def resolve_hierarchy(
    ctx: Context,
    model: ProjectModel,
    *,
    preset_rows: dict[str, list[ItemRow]] | None = None,
) -> tuple[Group, ...]:
    """Every declared hierarchy level, its containers and their order key."""
    groups: list[Group] = []
    reach = ctx.reach
    for level in model.operating.hierarchy:
        with_seq = level.order is None
        rows = _rows(ctx, (level.preset,), with_seq=with_seq)
        rows.sort(key=lambda row: _order_key(row, level.order))
        if preset_rows is not None:
            preset_rows[level.preset] = rows
        outside = 0
        if reach is not None and not reach.read_all:
            outside = _count(ctx, (level.preset,)) - len(rows)
        groups.append(
            Group(
                preset=level.preset,
                label=label(model, level.preset, plural=True),
                order=level.order,
                items=tuple(rows),
                outside_reach=outside,
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
    all_items: Sequence[Any] | None = None,
    held_map: Mapping[str, str] | None = None,
    parents: dict[str, tuple[str, ...]] | None = None,
    preset_rows: dict[str, list[ItemRow]] | None = None,
) -> QueueView:
    """The operating queue: presets, eligibility, focus priority, stable order."""
    focus = resolve_focus(ctx, model, into=into, preset_rows=preset_rows)
    focus_slug = None if focus.focus is None else focus.focus.slug
    presets = model.operating.queue.presets
    if all:
        presets = tuple(
            preset
            for preset in sorted(ctx.pack.presets)
            if ctx.pack.presets[preset].primitive == "item"
        )
    if (
        preset_rows is not None
        and len(presets) == 1
        and presets[0] in preset_rows
    ):
        rows = preset_rows[presets[0]]
    else:
        rows = _rows(ctx, presets, with_seq=False)
        if preset_rows is not None and len(presets) == 1:
            preset_rows[presets[0]] = rows
    if parents is None:
        parents = _parents(ctx)
    grouped = _group_order(ctx, model, preset_rows=preset_rows)
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
    from context_rail.context import recipes

    items = list(all_items) if all_items is not None else recipes.item_rows(ctx.index, ctx.pack)
    held = dict(held_map) if held_map is not None else structure.blocked(items)
    finished = {
        item.slug
        for item in items
        if item.category in structure.FINISHED_CATEGORIES
    }
    blocked = {
        slug: reason for slug, reason in held.items() if slug not in finished
    }
    eligible: list[QueueItem] = []
    omitted = 0
    action_cache: dict[tuple[str, str], tuple[str, ...]] = {}
    has_actions = bool(ctx.pack.actions)
    workflows: dict[str, Any] = {}
    for row in rows:
        if row.slug in blocked or row.category in ("done", "dropped", "blocked"):
            continue
        if not has_actions:
            actions: tuple[str, ...] = ()
        else:
            cache_key = (row.preset, row.status)
            actions = action_cache.get(cache_key)  # type: ignore[assignment]
            if actions is None:
                # Actions without item-specific requirements depend only on preset+status.
                actions = applicable_actions(ctx, model, row, actor)
                if not any(
                    a.requires.children
                    or a.requires.check
                    or a.requires.review
                    or a.requires.field
                    for a in ctx.pack.actions.values()
                ):
                    action_cache[cache_key] = actions
        if model.operating.queue.require == "action":
            if not actions:
                omitted += 1
                continue
        else:
            workflow = workflows.get(row.preset)
            if workflow is None:
                workflow = ctx.pack.workflow_for(row.preset)
                workflows[row.preset] = workflow
            if not workflow.transitions.get(row.status, ()):
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
        blocked={
            row.slug: held_reason(ctx, blocked[row.slug])
            for row in rows
            if row.slug in blocked
        },
    )


def held_reason(ctx: Context, reason: str) -> str:
    """A blocker without the name of a dependency the caller may not read."""
    reach = ctx.reach
    if reach is None or not reason.startswith("depends_on:"):
        return reason
    dependency = reason.partition(":")[2]
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1", (dependency,)
    ).fetchone()
    if row is not None and reach.can_read(dependency, path=row[0], preset=row[1]):
        return reason
    return "depends_on"


def applicable_actions(
    ctx: Context, model: ProjectModel, row: ItemRow, actor: str
) -> tuple[str, ...]:
    """Every declared action that applies to this row's preset, state and actor."""
    if not ctx.pack.actions:
        return ()
    from context_rail.core.tool_names import canonical_methods
    from context_rail.gateway.tools import TOOLS

    specs = {tool.name: tool for tool in TOOLS}
    profile = getattr(ctx, "profile", None)
    # An omitted method list inherits rather than suppresses: in a store with
    # roles the role decides, and a store without roles never parses an empty one.
    declared = profile.allowed_methods if profile is not None else ()
    methods = canonical_methods(declared) if declared else None
    identity = getattr(ctx, "identity", None)
    role_actions: frozenset[str] | None = None
    role_tools_set: frozenset[str] | None = None
    if getattr(ctx, "role_mode", False) and identity is not None and not identity.human:
        role = getattr(identity, "role", None)
        if role is None:
            return ()
        from context_rail.model.roles import role_tools

        role_tools_set = role_tools(role)
        allowed_actions = role.can.methods.get("actions")
        if allowed_actions is True:
            role_actions = frozenset(ctx.pack.actions)
        elif isinstance(allowed_actions, (list, tuple)):
            role_actions = frozenset(allowed_actions)
        else:
            role_actions = frozenset()
    names: list[str] = []
    for action in ctx.pack.actions.values():
        if action.on != "*" and action.on != row.preset:
            continue
        if action.from_states and row.status not in action.from_states:
            continue
        if role_actions is not None and action.name not in role_actions:
            continue
        if methods is not None and action.tool not in methods:
            continue
        spec = specs.get(action.tool)
        if (
            role_tools_set is not None
            and spec is not None
            and spec.permission != "read"
            and action.tool not in role_tools_set
        ):
            continue
        if spec is not None and rights.allowed(spec.permission, actor) is not None:
            continue
        if not _requires_met(ctx, action, row.slug):
            continue
        names.append(action.name)
    return tuple(sorted(names))


def resolve_progress(
    ctx: Context,
    model: ProjectModel,
    group: str | None = None,
    *,
    parents: dict[str, tuple[str, ...]] | None = None,
    preset_rows: dict[str, list[ItemRow]] | None = None,
) -> ProgressView:
    """Workflow progress over ``progress.of``, grouped along the hierarchy."""
    policy = model.operating.progress
    if (
        preset_rows is not None
        and len(policy.of) == 1
        and policy.of[0] in preset_rows
    ):
        rows = preset_rows[policy.of[0]]
    else:
        rows = _rows(ctx, policy.of, with_seq=False)
        if preset_rows is not None and len(policy.of) == 1:
            preset_rows[policy.of[0]] = rows
    counts: dict[str, int] = {}
    complete = 0
    complete_cache: dict[tuple[str, str], bool] = {}
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1
        key = (row.preset, row.status)
        done = complete_cache.get(key)
        if done is None:
            done = _is_complete(ctx, model, row)
            complete_cache[key] = done
        if done:
            complete += 1
    group_preset = group or policy.group
    children: list[dict[str, Any]] = []
    if group_preset is not None:
        if preset_rows is not None and group_preset in preset_rows:
            containers = preset_rows[group_preset]
        else:
            order_field = _group_order_field(model, group_preset)
            containers = _rows(ctx, (group_preset,), with_seq=order_field is None)
            containers.sort(key=lambda row: _order_key(row, order_field))
            if preset_rows is not None:
                preset_rows[group_preset] = containers
        if parents is None:
            parents = _parents(ctx)
        members: dict[str, list[ItemRow]] = {row.slug: [] for row in containers}
        for row in rows:
            parent = _nearest_ancestor(row.slug, parents, members)
            if parent is not None:
                members[parent].append(row)
        for container in containers:
            bucket = members[container.slug]
            bucket_complete = 0
            for row in bucket:
                key = (row.preset, row.status)
                done = complete_cache.get(key)
                if done is None:
                    done = _is_complete(ctx, model, row)
                    complete_cache[key] = done
                if done:
                    bucket_complete += 1
            children.append(
                {
                    "slug": container.slug,
                    "title": container.title,
                    "total": len(bucket),
                    "complete": bucket_complete,
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
        of=tuple(policy.of),
        rule=policy.complete,
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


def _candidates(
    ctx: Context,
    model: ProjectModel,
    *,
    preset_rows: dict[str, list[ItemRow]] | None = None,
) -> list[ItemRow]:
    policy = model.operating.focus
    if policy is None:
        return []
    if preset_rows is not None and policy.preset in preset_rows:
        rows = preset_rows[policy.preset]
    else:
        rows = _rows(ctx, (policy.preset,), with_seq=policy.order is None)
    return sorted(
        (row for row in rows if row.category == policy.category),
        key=lambda row: _order_key(row, policy.order),
    )


def _group_order(
    ctx: Context,
    model: ProjectModel,
    *,
    preset_rows: dict[str, list[ItemRow]] | None = None,
) -> dict[str, int]:
    order: dict[str, int] = {}
    position = 0
    for level in model.operating.hierarchy:
        if preset_rows is not None and level.preset in preset_rows:
            rows = preset_rows[level.preset]
        else:
            rows = sorted(
                _rows(ctx, (level.preset,), with_seq=level.order is None),
                key=lambda item: _order_key(item, level.order),
            )
            if preset_rows is not None:
                preset_rows[level.preset] = rows
        for row in rows:
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
    direct = parents.get(item.slug)
    if not direct:
        parent_order = len(grouped)
    elif len(direct) == 1 and direct[0] not in parents:
        parent_order = grouped.get(direct[0], len(grouped))
    else:
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
    direct = parents.get(slug)
    if not direct:
        return []
    found: set[str] = set()
    ordered: list[str] = []
    idx = 0
    ordered.extend(direct)
    found.update(direct)
    while idx < len(ordered):
        current = ordered[idx]
        idx += 1
        for nxt in parents.get(current, ()):
            if nxt not in found:
                found.add(nxt)
                ordered.append(nxt)
    return ordered


def _nearest_ancestor(
    slug: str,
    parents: dict[str, tuple[str, ...]],
    containers: dict[str, list[ItemRow]],
) -> str | None:
    direct = parents.get(slug)
    if not direct:
        return None
    if len(direct) == 1 and direct[0] not in parents:
        return direct[0] if direct[0] in containers else None
    for candidate in _ancestors(slug, parents):
        if candidate in containers:
            return candidate
    return None


def _one(ctx: Context, slug: str) -> ItemRow | None:
    rows = _rows(ctx, None, slug=slug)
    return rows[0] if rows else None


def _count(ctx: Context, presets: tuple[str, ...]) -> int:
    """How many items the presets hold, so a caller can name what reach hid."""
    if not presets:
        return 0
    return int(
        ctx.index.conn.execute(
            "SELECT COUNT(*) FROM docs d WHERE d.primitive = 'item' AND d.broken = 0 "
            f"AND d.preset IN ({', '.join('?' * len(presets))})",
            presets,
        ).fetchone()[0]
    )


def _rows(
    ctx: Context,
    presets: tuple[str, ...] | None,
    *,
    slug: str | None = None,
    with_seq: bool = True,
) -> list[ItemRow]:
    """The items one caller may read, in the resolver's own shape.

    Reach applies here, at the one selection every operating read shares, so a
    queue, a progress total and a focus candidate can never name a document the
    caller may not read.
    """
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
        "d.created, d.updated, d.path, d.etag, d.rowid FROM docs d WHERE "
        + " AND ".join(where)
        + " ORDER BY d.slug",
        params,
    ).fetchall()
    reach = ctx.reach
    if reach is not None:
        rows = [
            row
            for row in rows
            if reach.can_read(row[0], path=row[8], preset=row[2])
        ]
    if not rows:
        return []
    paths = [row[8] for row in rows]
    slugs = [row[0] for row in rows]
    fields: dict[str, dict[str, Any]] = {}
    if len(paths) == 1:
        path_map: dict[str, Any] = {}
        for key, value in index.conn.execute(
            "SELECT key, value_json FROM fields WHERE path = ?",
            (paths[0],),
        ):
            path_map[key] = json.loads(value)
        fields[paths[0]] = path_map
    else:
        for start in range(0, len(paths), 500):
            chunk = paths[start : start + 500]
            for path, key, value in index.conn.execute(
                "SELECT path, key, value_json FROM fields WHERE path IN "
                f"({', '.join('?' * len(chunk))})",
                chunk,
            ):
                bucket = fields.get(path)
                if bucket is None:
                    fields[path] = {key: json.loads(value)}
                else:
                    bucket[key] = json.loads(value)
    created_seq: dict[str, str] = {}
    if with_seq:
        for start in range(0, len(slugs), 500):
            chunk = slugs[start : start + 500]
            for item_slug, record_slug in index.conn.execute(
                "SELECT r.dst, MIN(d.slug) FROM relations r "
                "JOIN docs d ON d.path = r.src_path "
                "JOIN fields f ON f.path = d.path AND f.key = 'kind' "
                "WHERE r.rel = 'about' AND d.primitive = 'record' AND d.broken = 0 "
                "AND f.value_json = '\"doc.created\"' "
                f"AND r.dst IN ({', '.join('?' * len(chunk))}) "
                "GROUP BY r.dst",
                chunk,
            ):
                if record_slug:
                    created_seq[item_slug] = record_slug
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
            seq=(0, created_seq[row[0]]) if row[0] in created_seq else (1, row[10]),
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
    return (_sortable(raw), row.created or "", row.seq, row.slug)


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
    "held_reason",
    "item_row",
    "outside_focus",
    "require_focus",
    "role_for",
]
