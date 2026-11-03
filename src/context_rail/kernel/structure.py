"""Structure: what can be worked on, what holds it back, and how score rolls up.

Every rule here reads categories and relations, never status or tag names.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from context_rail.model.primitives import FINISHED_CATEGORIES, WORKABLE_CATEGORIES

__all__ = [
    "FINISHED_CATEGORIES",
    "WORKABLE_CATEGORIES",
    "ItemRow",
    "Score",
    "blocked",
    "cycles",
    "executable",
    "rollup",
    "stuck",
]


@dataclass(frozen=True)
class ItemRow:
    """The slice of an item the structure rules need."""

    slug: str
    category: str | None
    part_of: tuple[str, ...]
    depends_on: tuple[str, ...]
    last_activity_ms: int | None
    dispatchable: bool = True
    preset: str | None = None
    workflow: str = "default"
    container: bool | None = None
    status: str | None = None


@dataclass(frozen=True)
class Score:
    """Proven checks over total checks."""

    proven: int
    total: int


def executable(items: list[ItemRow], held: dict[str, str] | None = None) -> list[str]:
    """Leaf items that are open, dispatchable and unblocked, sorted.

    ``held`` lets a caller that already asked :func:`blocked` reuse the answer
    instead of walking every dependency again.
    """
    if held is None:
        held = blocked(items)
    parents = {parent for item in items for parent in item.part_of}

    def is_container(item: ItemRow) -> bool:
        if item.container is not None:
            return item.container
        return item.slug in parents

    return sorted(
        item.slug
        for item in items
        if item.category in WORKABLE_CATEGORIES
        and item.dispatchable
        and item.slug not in held
        and not is_container(item)
    )


def blocked(items: list[ItemRow]) -> dict[str, str]:
    """Slug to the reason it cannot proceed: its status, its first open dependency
    or a dependency cycle.

    Every item with an open dependency gets an entry, finished items included, so
    a caller reporting what is blocked must drop ``FINISHED_CATEGORIES`` itself.
    """
    categories = {item.slug: item.category for item in items}
    loops = cycles(items)
    reasons: dict[str, str] = {}
    for item in items:
        if item.slug in loops:
            reasons[item.slug] = "cycle"
            continue
        if item.category == "blocked":
            reasons[item.slug] = "status"
            continue
        open_deps = [
            dep for dep in item.depends_on if categories.get(dep) not in FINISHED_CATEGORIES
        ]
        if open_deps:
            reasons[item.slug] = f"depends_on:{min(open_deps)}"
    return reasons


def cycles(items: list[ItemRow]) -> frozenset[str]:
    """Every slug that lies on a ``depends_on`` cycle (iterative, bounded memory)."""
    edges = {item.slug: tuple(item.depends_on) for item in items}
    color: dict[str, int] = {}
    found: set[str] = set()
    for root in edges:
        if color.get(root) == 2 or not edges[root]:
            # A node with no outgoing dependency cannot lie on a cycle.
            continue
        color[root] = 1
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            node, position = stack[-1]
            if position == 0:
                path.append(node)
            neighbours = edges.get(node, ())
            if position < len(neighbours):
                stack[-1] = (node, position + 1)
                neighbour = neighbours[position]
                state = color.get(neighbour, 0)
                if state == 1:
                    if neighbour in path:
                        found.update(path[path.index(neighbour) :])
                elif state == 0:
                    color[neighbour] = 1
                    stack.append((neighbour, 0))
            else:
                color[node] = 2
                stack.pop()
                path.pop()
    return frozenset(found)


def stuck(items: list[ItemRow], now_ms: int, stuck_ms: int) -> list[str]:
    """Active items with no activity inside the window, sorted."""
    cutoff = now_ms - stuck_ms
    return sorted(
        item.slug
        for item in items
        if item.category == "active"
        and (item.last_activity_ms is None or item.last_activity_ms < cutoff)
    )


def rollup(
    items: list[ItemRow], verifies: dict[str, str], verdicts: dict[str, bool | None]
) -> dict[str, Score]:
    """Proven-over-total checks per item, a parent's score summing its descendants."""
    children = _children(items)
    _refuse_cycles(items, children)
    own = _own_scores(items, verifies, verdicts)
    scores: dict[str, Score] = {}
    for item in items:
        reachable = _descendants(item.slug, children)
        scores[item.slug] = Score(
            proven=sum(own[slug].proven for slug in reachable),
            total=sum(own[slug].total for slug in reachable),
        )
    return scores


def _children(items: list[ItemRow]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    known = {item.slug for item in items}
    for item in items:
        for parent in item.part_of:
            if parent in known:
                children.setdefault(parent, []).append(item.slug)
    return children


def _refuse_cycles(items: list[ItemRow], children: dict[str, list[str]]) -> None:
    """Walk part_of downwards; reaching a node still on the stack means a cycle."""
    open_nodes: set[str] = set()
    closed: set[str] = set()
    for item in items:
        if item.slug in open_nodes or item.slug in closed:
            continue
        stack: list[tuple[str, Iterator[str]]] = [(item.slug, iter(children.get(item.slug, ())))]
        open_nodes.add(item.slug)
        while stack:
            node, pending = stack[-1]
            child = next(pending, None)
            if child is None:
                stack.pop()
                open_nodes.discard(node)
                closed.add(node)
            elif child in open_nodes:
                raise ValueError("cycle")
            elif child not in closed:
                open_nodes.add(child)
                stack.append((child, iter(children.get(child, ()))))


def _descendants(slug: str, children: dict[str, list[str]]) -> set[str]:
    reachable = {slug}
    pending = [slug]
    while pending:
        node = pending.pop()
        for child in children.get(node, ()):
            if child not in reachable:
                reachable.add(child)
                pending.append(child)
    return reachable


def _own_scores(
    items: list[ItemRow], verifies: dict[str, str], verdicts: dict[str, bool | None]
) -> dict[str, Score]:
    totals = {item.slug: 0 for item in items}
    proven = {item.slug: 0 for item in items}
    for check, target in verifies.items():
        if target not in totals:
            continue
        totals[target] += 1
        if verdicts.get(check) is True:
            proven[target] += 1
    return {slug: Score(proven=proven[slug], total=totals[slug]) for slug in totals}
