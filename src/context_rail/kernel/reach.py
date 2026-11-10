"""Reach closure: pure walks over a work graph given as plain mappings.

Every walk is iterative and keeps a visited set, so a cycle in the graph ends
the walk instead of looping. The index feeds these functions; nothing here
touches it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from context_rail.model.patterns import prefix_matches

__all__ = ["ancestors", "descendants", "one_hop", "under_prefix"]


def descendants(root: str, children: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """``root`` and everything below it through ``children``."""
    seen = {root}
    pending = [root]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in seen:
                seen.add(child)
                pending.append(child)
    return frozenset(seen)


def ancestors(
    slug: str, parents: Mapping[str, Sequence[str]], up: int | None
) -> frozenset[str]:
    """Every ancestor of ``slug`` within ``up`` levels (``None`` is unbounded)."""
    seen = {slug}
    found: set[str] = set()
    frontier = [slug]
    level = 0
    while frontier and (up is None or level < up):
        level += 1
        following: list[str] = []
        for current in frontier:
            for parent in parents.get(current, ()):
                if parent not in seen:
                    seen.add(parent)
                    found.add(parent)
                    following.append(parent)
        frontier = following
    return frozenset(found)


def one_hop(
    sources: Iterable[str], edges: Mapping[str, Sequence[str]]
) -> frozenset[str]:
    """The targets of the outgoing edges of ``sources``, and nothing further."""
    return frozenset(target for source in sources for target in edges.get(source, ()))


def under_prefix(prefix: str, path: str) -> bool:
    """Whether ``path`` is ``prefix`` or lies below it, after normalising ``\\``."""
    return prefix_matches(prefix, path)
