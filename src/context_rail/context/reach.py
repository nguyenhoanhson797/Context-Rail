"""What one seat may read and write, resolved from the work graph.

A role declares reach as fixed terms (``project``, ``anchor``, ``up``,
``related``, ``preset``, ``path``) and the seat supplies the anchor. This module
turns the two into a ``ReachSet`` through the index and the pure walks in
``kernel.reach``. A seat whose anchor is missing, of the wrong kind, or whose
pattern token renders empty has no reach at all: ``ReachError`` is raised and
the caller refuses, never widening to a looser pattern.

The engine never branches on a role, preset or slug name here; every name comes
from the role the pack declared.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from context_rail.kernel.reach import ancestors, descendants, one_hop, under_prefix
from context_rail.kernel.seats import LiveSeat
from context_rail.model.errors import ModelError
from context_rail.model.pack import Pack
from context_rail.model.patterns import Pattern, PatternToken, render_pattern
from context_rail.model.roles import ReachTerm, Role
from context_rail.store.index import Index

__all__ = ["UNRESTRICTED", "ReachError", "ReachSet", "resolve_reach"]

_PART_OF = "part_of"
_PROJECT = "project"


class ReachError(ValueError):
    """A seat's reach cannot be resolved: bad anchor or an empty token."""


@dataclass(frozen=True)
class ReachSet:
    """The documents, folders and code a seat may read and write.

    Slug sets come from graph terms. Preset and path terms are matched against
    the ``preset`` and ``path`` the caller passes, so a caller that holds a
    document row should always pass both; a missing one only fails closed.
    """

    seat: str | None
    unrestricted: bool
    read_slugs: frozenset[str] = frozenset()
    write_slugs: frozenset[str] = frozenset()
    read_prefixes: tuple[str, ...] = ()
    write_prefixes: tuple[str, ...] = ()
    read_presets: frozenset[str] = frozenset()
    write_presets: frozenset[str] = frozenset()
    code_read: tuple[str, ...] = ()
    code_write: tuple[str, ...] = ()
    read_all: bool = False
    write_all: bool = False
    anchor: str | None = None

    def can_read(
        self, slug: str, *, path: str | None = None, preset: str | None = None
    ) -> bool:
        """Whether the document is in read reach."""
        return (
            self.unrestricted
            or self.read_all
            or _covers(
                slug,
                path,
                preset,
                self.read_slugs,
                self.read_prefixes,
                self.read_presets,
            )
        )

    def can_write(
        self, slug: str, *, path: str | None = None, preset: str | None = None
    ) -> bool:
        """Whether the document is in write reach."""
        return (
            self.unrestricted
            or self.write_all
            or _covers(
                slug,
                path,
                preset,
                self.write_slugs,
                self.write_prefixes,
                self.write_presets,
            )
        )

    def can_read_code(self, rel: str) -> bool:
        """Whether the repository-relative path is in code read reach."""
        return self.unrestricted or _under_any(self.code_read, rel)

    def can_write_code(self, rel: str) -> bool:
        """Whether the repository-relative path is in code write reach."""
        return self.unrestricted or _under_any(self.code_write, rel)

    def intersect(self, other: "ReachSet") -> "ReachSet":
        """The narrower reach both sets hold; either unrestricted gives the other."""
        if self.unrestricted:
            return other
        if other.unrestricted:
            return self
        read_slugs, read_prefixes, read_presets = _intersect_read(
            self.read_all,
            other.read_all,
            self.read_slugs,
            self.read_prefixes,
            self.read_presets,
            other.read_slugs,
            other.read_prefixes,
            other.read_presets,
        )
        write_slugs, write_prefixes, write_presets = _intersect_read(
            self.write_all,
            other.write_all,
            self.write_slugs,
            self.write_prefixes,
            self.write_presets,
            other.write_slugs,
            other.write_prefixes,
            other.write_presets,
        )
        return ReachSet(
            seat=self.seat,
            unrestricted=False,
            read_slugs=read_slugs,
            write_slugs=write_slugs,
            read_prefixes=read_prefixes,
            write_prefixes=write_prefixes,
            read_presets=read_presets,
            write_presets=write_presets,
            code_read=_intersect_code(self.code_read, other.code_read),
            code_write=_intersect_code(self.code_write, other.code_write),
            read_all=self.read_all and other.read_all,
            write_all=self.write_all and other.write_all,
            anchor=self.anchor,
        )


def _intersect_read(
    first_all: bool,
    second_all: bool,
    first_slugs: frozenset[str],
    first_prefixes: tuple[str, ...],
    first_presets: frozenset[str],
    second_slugs: frozenset[str],
    second_prefixes: tuple[str, ...],
    second_presets: frozenset[str],
) -> tuple[frozenset[str], tuple[str, ...], frozenset[str]]:
    """Intersect two term sets, where one side's ``all`` gives the other side."""
    if first_all:
        return second_slugs, second_prefixes, second_presets
    if second_all:
        return first_slugs, first_prefixes, first_presets
    return (
        first_slugs & second_slugs,
        _narrow(first_prefixes, second_prefixes),
        first_presets & second_presets,
    )


def _narrow(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """The more specific prefix for each overlapping pair."""
    found: set[str] = set()
    for left in first:
        for right in second:
            if under_prefix(left, right):
                found.add(right)
            elif under_prefix(right, left):
                found.add(left)
    return tuple(sorted(found))


def _intersect_code(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """Intersect two code-pattern sets; ``**`` covers and gives the other side."""
    if not first:
        return ()
    if not second:
        return ()
    if first == ("**",):
        return second
    if second == ("**",):
        return first
    found: set[str] = set()
    for left in first:
        for right in second:
            if left == right:
                found.add(left)
            elif under_prefix(left, right):
                found.add(right)
            elif under_prefix(right, left):
                found.add(left)
    return tuple(sorted(found))


UNRESTRICTED = ReachSet(seat=None, unrestricted=True)


def resolve_reach(index: Index, pack: Pack, role: Role, seat: LiveSeat) -> ReachSet:
    """The reach ``seat`` holds as ``role``; raises ``ReachError`` when it has none."""
    graph = _Graph(index, pack, role, seat)
    read = graph.terms(role.read)
    write = graph.terms(role.write)
    return ReachSet(
        seat=seat.seat,
        unrestricted=False,
        read_slugs=read.slugs,
        write_slugs=write.slugs,
        read_prefixes=read.prefixes,
        write_prefixes=write.prefixes,
        read_presets=read.presets,
        write_presets=write.presets,
        code_read=graph.code(role.code_read),
        code_write=graph.code(role.code_write),
        read_all=read.everything,
        write_all=write.everything,
        anchor=graph.anchor,
    )


@dataclass(frozen=True)
class _Resolved:
    slugs: frozenset[str]
    prefixes: tuple[str, ...]
    presets: frozenset[str]
    everything: bool


def _covers(
    slug: str,
    path: str | None,
    preset: str | None,
    slugs: frozenset[str],
    prefixes: tuple[str, ...],
    presets: frozenset[str],
) -> bool:
    if slug in slugs:
        return True
    if preset is not None and preset in presets:
        return True
    return path is not None and _under_any(prefixes, path)


def _under_any(prefixes: tuple[str, ...], path: str) -> bool:
    return any(under_prefix(prefix, path) for prefix in prefixes)


class _Graph:
    """One resolution's view of the index: edges and anchor documents, read once."""

    def __init__(self, index: Index, pack: Pack, role: Role, seat: LiveSeat) -> None:
        self._conn = index.conn
        self._role = role
        self._parents: dict[str, list[str]] | None = None
        self._children: dict[str, list[str]] | None = None
        self._docs: dict[str, tuple[str, str, str] | None] = {}
        self.anchor: str | None = None
        if role.anchor.kind == _PROJECT:
            if seat.anchor != _PROJECT:
                raise ReachError(
                    f"role {role.name!r} sits on the project; its seat anchor "
                    f"must be {_PROJECT!r}, not {seat.anchor!r}"
                )
            return
        row = self._doc(seat.anchor)
        if row is None:
            raise ReachError(f"anchor {seat.anchor!r} is not a document in this store")
        preset = row[2]
        if preset not in role.anchor.presets or preset not in pack.presets:
            wanted = ", ".join(role.anchor.presets)
            raise ReachError(
                f"anchor {seat.anchor!r} is a {preset!r}, but role {role.name!r} "
                f"needs one of: {wanted}"
            )
        self.anchor = seat.anchor

    def terms(self, terms: tuple[ReachTerm, ...]) -> _Resolved:
        slugs: set[str] = set()
        prefixes: list[str] = []
        presets: set[str] = set()
        everything = False
        for term in terms:
            if term.kind == _PROJECT:
                everything = True
            elif term.kind == "anchor":
                if self.anchor is None:
                    everything = True
                else:
                    slugs |= self._subtree()
            elif term.kind == "up":
                if self.anchor is not None:
                    slugs |= ancestors(self.anchor, self._edges()[0], term.value)
            elif term.kind == "related":
                if self.anchor is not None:
                    slugs |= self._related(tuple(term.value))
            elif term.kind == "preset":
                presets |= set(term.value)
            elif term.kind == "path":
                prefix = self._render(term.value, slugify_values=True)
                if prefix:
                    prefixes.append(prefix)
                else:
                    everything = True
        return _Resolved(
            frozenset(slugs),
            tuple(dict.fromkeys(prefixes)),
            frozenset(presets),
            everything,
        )

    def code(self, patterns: tuple[Pattern, ...]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                self._render(pattern, slugify_values=False) for pattern in patterns
            )
        )

    def _edges(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        if self._parents is None or self._children is None:
            parents: dict[str, list[str]] = defaultdict(list)
            children: dict[str, list[str]] = defaultdict(list)
            for src, dst in self._conn.execute(
                "SELECT src, dst FROM relations WHERE rel = ?", (_PART_OF,)
            ):
                parents[src].append(dst)
                children[dst].append(src)
            self._parents, self._children = dict(parents), dict(children)
        return self._parents, self._children

    def _subtree(self) -> frozenset[str]:
        assert self.anchor is not None
        return descendants(self.anchor, self._edges()[1])

    def _related(self, relations: tuple[str, ...]) -> frozenset[str]:
        marks = ", ".join("?" * len(relations))
        edges: dict[str, list[str]] = defaultdict(list)
        for src, dst in self._conn.execute(
            f"SELECT src, dst FROM relations WHERE rel IN ({marks})", relations
        ):
            edges[src].append(dst)
        return one_hop(self._subtree(), edges)

    def _doc(self, slug: str) -> tuple[str, str, str] | None:
        if slug not in self._docs:
            row = self._conn.execute(
                "SELECT path, title, preset FROM docs WHERE slug = ?", (slug,)
            ).fetchone()
            self._docs[slug] = None if row is None else (row[0], row[1], row[2])
        return self._docs[slug]

    def _render(self, pattern: Pattern, *, slugify_values: bool) -> str:
        try:
            return render_pattern(pattern, self._lookup, slugify_values=slugify_values)
        except ModelError as error:
            raise ReachError(f"role {self._role.name!r}: {error}") from error

    def _lookup(self, token: PatternToken) -> str | None:
        slug = self._token_doc(token)
        return None if slug is None else self._field(slug, token.field)

    def _token_doc(self, token: PatternToken) -> str | None:
        if self.anchor is None:
            return None
        if token.kind == "anchor":
            return self.anchor
        row = self._doc(self.anchor)
        if row is not None and row[2] == token.preset:
            return self.anchor
        parents = self._edges()[0]
        reached = {self.anchor}
        level = 0
        while True:
            level += 1
            found = ancestors(self.anchor, parents, level) - reached
            if not found:
                return None
            reached |= found
            matches = sorted(
                slug
                for slug in found
                if (doc := self._doc(slug)) is not None and doc[2] == token.preset
            )
            if len(matches) > 1:
                raise ReachError(
                    f"role {self._role.name!r}: {token.preset!r} is ambiguous above "
                    f"{self.anchor!r}: {', '.join(matches)}"
                )
            if matches:
                return matches[0]

    def _field(self, slug: str, name: str) -> str | None:
        row = self._doc(slug)
        if row is None:
            return None
        if name == "slug":
            return slug
        if name == "title":
            return row[1]
        found = self._conn.execute(
            "SELECT value_json FROM fields WHERE path = ? AND key = ?", (row[0], name)
        ).fetchone()
        return None if found is None else _text(json.loads(found[0]))


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None
