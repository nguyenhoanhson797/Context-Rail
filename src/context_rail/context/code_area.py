"""The predicted code area of an item: what the work will touch and who depends on it.

Everything here is advisory. A missing or failed graph never stops the work; it
shows up as a line in the text that carries the area.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

from context_rail.model.codegraph import (
    Neighbors,
    effective_code_graph,
    resolve,
    upstream,
)
from context_rail.model.cto import CODE_AREA_MAX

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = [
    "CODE_AREA_CHARS",
    "CODE_AREA_ENTRIES",
    "NO_AREA",
    "Area",
    "configured_graph",
    "fit_lines",
    "predict_area",
    "render_area",
]

CODE_AREA_CHARS = 2000
CODE_AREA_ENTRIES = CODE_AREA_MAX
NO_AREA = "no code area predicted (mention a path or path:Symbol in the item)"
_TOKEN = re.compile(r"[\w.\-/]+\.[A-Za-z0-9]+(?::[A-Za-z_][\w.]*)?")
_OVERLAPS_SHOWN = 3
_HISTORY_FILES = 5


@dataclass(frozen=True)
class Area:
    """Seed nodes the work will touch and the nodes that depend on them."""

    seeds: tuple[str, ...]
    callers: tuple[str, ...]

    def entries(self) -> tuple[str, ...]:
        return (self.seeds + self.callers)[:CODE_AREA_ENTRIES]


def predict_area(
    graph: Neighbors,
    *,
    text: str,
    globs: Sequence[str] = (),
    earlier: Sequence[str] = (),
) -> Area:
    """Seeds from mentions in ``text``, check input globs, then earlier changes."""
    seeds: list[str] = []

    def add(ids: Sequence[str]) -> None:
        for node_id in ids:
            if node_id not in seeds:
                seeds.append(node_id)

    for token in _TOKEN.findall(text):
        found = resolve(graph, token.rstrip("."))
        if len(found) == 1:
            add(found)
    files = graph.files()
    for pattern in globs:
        add([path for path in files if fnmatchcase(path, pattern)])
    for path in earlier:
        add(resolve(graph, path))
    seeds = seeds[:CODE_AREA_ENTRIES]

    targets = list(seeds)
    for node_id in seeds:
        node = graph.node(node_id)
        if node is not None and node.kind == "file":
            targets += [inner.id for inner in graph.by_file(node_id) if inner.kind != "file"]
    callers = tuple(
        node_id
        for node_id in upstream(graph, targets, depth=1, limit=CODE_AREA_ENTRIES)
        if node_id not in seeds
    )
    return Area(tuple(seeds), callers)


def render_area(
    *,
    graph_line: str,
    area: Area,
    overlaps: Sequence[dict[str, Any]] = (),
    history: Sequence[str] = (),
) -> str:
    """The area as text, never longer than :data:`CODE_AREA_CHARS`."""
    if not area.seeds:
        return NO_AREA
    lists: list[tuple[str, list[str]]] = [
        ("likely touch", list(area.seeds)),
        ("called by", list(area.callers)),
    ]
    if history:
        lists.append(("history", list(history[:_HISTORY_FILES])))
    tails = [
        f"overlap: {entry['item']} (active) predicted {', '.join(entry['files'])} "
        "-> coordinate or merge after it"
        for entry in list(overlaps)[:_OVERLAPS_SHOWN]
    ]
    return fit_lines([graph_line], lists, tails)


def configured_graph(ctx: Context) -> str:
    """The resolved provider choice for this store."""
    return effective_code_graph(ctx.manifest.code_graph, ctx.pack.policy.get("code_graph"))


def fit_lines(
    head: Sequence[str], lists: list[tuple[str, list[str]]], tails: Sequence[str] = ()
) -> str:
    """Join the lines, dropping entries from the longest list until it fits."""
    dropped = [0] * len(lists)
    while True:
        lines = list(head)
        for (label, entries), gone in zip(lists, dropped, strict=True):
            if entries:
                more = f" (+{gone} more)" if gone else ""
                lines.append(f"{label}: {', '.join(entries)}{more}")
        lines += tails
        text = "\n".join(lines)
        if len(text) <= CODE_AREA_CHARS:
            return text
        longest = max(
            (index for index, (_label, entries) in enumerate(lists) if len(entries) > 1),
            key=lambda index: len(", ".join(lists[index][1])),
            default=None,
        )
        if longest is None:
            return text[: CODE_AREA_CHARS - 1] + "…"
        lists[longest][1].pop()
        dropped[longest] += 1
