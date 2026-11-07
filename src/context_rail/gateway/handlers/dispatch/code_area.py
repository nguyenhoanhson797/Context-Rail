"""The predicted code area of a dispatch: what it will touch and who depends on it.

Everything here is advisory. A missing or failed graph never stops a dispatch;
it shows up as a line in the work order and a warning on the result.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.model.codegraph import (
    Neighbors,
    effective_code_graph,
    resolve,
    upstream,
)
from context_rail.model.cto import CODE_AREA_MAX
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner.codegraph import Ensured, ensure_snapshot
from context_rail.store import query
from context_rail.store.codegraph import open_graph

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.store.doc import Doc

__all__ = [
    "CODE_AREA_CHARS",
    "CODE_AREA_ENTRIES",
    "Area",
    "Prepared",
    "predict_area",
    "prepare",
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


@dataclass(frozen=True)
class Prepared:
    """The work-order section, the area to record, and any warnings."""

    section: str
    entries: tuple[str, ...]
    warnings: tuple[str, ...]


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
    """The work-order text, never longer than :data:`CODE_AREA_CHARS`."""
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
    dropped = [0] * len(lists)
    while True:
        lines = [graph_line]
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


def prepare(
    ctx: Context,
    *,
    slug: str,
    doc: Doc,
    dispatch_id: str,
    worktree: Path,
    base: str,
) -> Prepared:
    """Build the base snapshot, predict the area, and record it in progress."""
    store = Path(ctx.store)
    configured = effective_code_graph(
        ctx.manifest.code_graph, ctx.pack.policy.get("code_graph")
    )
    if configured == "none":
        return Prepared("(none)", (), ())
    others = [
        entry
        for entry in local_dispatch.active_dispatches(store)
        if entry["dispatch_id"] != dispatch_id
    ]
    ensured = ensure_snapshot(
        store,
        worktree=worktree,
        commit=base,
        configured=configured,
        protect=[entry["base_commit"] for entry in others if entry["base_commit"]],
    )
    graph = open_graph(store, base) if ensured.provider is not None else None
    if graph is None:
        reason = ensured.warning or "no snapshot for the base commit"
        return Prepared(f"code graph unavailable: {reason}", (), (reason,))
    with graph:
        earlier = _earlier_files(ctx, slug)
        globs = sorted(
            {glob for row in query.check_states(ctx.index, slug) for glob in row["inputs"]}
        )
        area = predict_area(
            graph, text=f"{doc.title or ''}\n{doc.body}", globs=globs, earlier=earlier
        )
    entries = area.entries()
    overlaps = _overlaps(entries, others)
    if entries:
        local_dispatch.write_progress(store, dispatch_id, code_area=list(entries))
    section = render_area(
        graph_line=_graph_line(ensured, base),
        area=area,
        overlaps=overlaps,
        history=earlier,
    )
    warnings = tuple(
        f"{entry['item']} is active and predicted overlapping code: "
        + ", ".join(entry["files"])
        for entry in overlaps
    )
    return Prepared(section, entries, warnings)


def _graph_line(ensured: Ensured, base: str) -> str:
    provider = ensured.provider or "builtin"
    if ensured.fallback:
        return (
            f"graph: {provider} @{base[:7]} "
            f"(fallback: {ensured.fallback} is not available)"
        )
    return f"graph: {provider} @{base[:7]} (fresh)"


def _earlier_files(ctx: Context, slug: str) -> list[str]:
    files: list[str] = []
    for fact in query.run_facts(ctx.index, slug):
        if fact["role"] != "implementer":
            continue
        record = query.run_record(ctx.index, fact["run"])
        for path in (record or {}).get("data", {}).get("files_changed") or ():
            if path not in files:
                files.append(path)
    return files


def _file_of(entry: str) -> str:
    return entry.partition(":")[0]


def _overlaps(
    entries: Sequence[str], others: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    mine = {_file_of(entry) for entry in entries}
    found = []
    for other in others:
        theirs = {_file_of(entry) for entry in other["code_area"]}
        shared = sorted(mine & theirs)
        if shared:
            found.append({"item": other["item"], "files": shared})
    return found
