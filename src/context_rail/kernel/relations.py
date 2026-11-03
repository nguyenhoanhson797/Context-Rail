"""Typed strong relations: which primitive each endpoint must carry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = [
    "PERMISSIVE_RELATIONS",
    "TYPED_RELATIONS",
    "endpoint_problem",
    "reachable",
]

#: Relations whose endpoint primitives are fixed in v4.0: (source, target).
TYPED_RELATIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "part_of": (frozenset({"item"}), frozenset({"item"})),
    "depends_on": (frozenset({"item"}), frozenset({"item"})),
    "verifies": (frozenset({"check"}), frozenset({"item"})),
    "owned_by": (frozenset({"item"}), frozenset({"actor"})),
}

#: Relations that stay permissive until a later tightening.
PERMISSIVE_RELATIONS = frozenset(
    {"about", "because_of", "supersedes", "resolves"}
)


def endpoint_problem(
    relation: str,
    source_primitive: str | None,
    target_primitive: str | None,
    *,
    source_slug: str | None = None,
    target_slug: str | None = None,
) -> str | None:
    """Why ``relation`` cannot join these two primitives, or ``None``."""
    if relation in PERMISSIVE_RELATIONS:
        return None
    allowed = TYPED_RELATIONS.get(relation)
    if allowed is None:
        return f"{relation!r} is not a declared relation"
    sources, targets = allowed
    if source_primitive not in sources:
        return (
            f"{relation!r} must start at a "
            + " or ".join(sorted(sources))
            + f", not a {source_primitive!r}"
        )
    if target_primitive not in targets:
        return (
            f"{relation!r} must point at a "
            + " or ".join(sorted(targets))
            + f", not a {target_primitive!r}"
        )
    if source_slug is not None and source_slug == target_slug:
        return f"{relation!r} may not point at its own document"
    return None


def reachable(
    edges: Mapping[str, Sequence[str]], start: str, target: str
) -> bool:
    """Whether ``target`` is reachable from ``start`` along the directed edges."""
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(edges.get(current, ()))
    return False
