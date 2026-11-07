"""Files and symbols as targets of ``why`` and ``show``.

A code target is answered from two caches: the code graph snapshot (who calls
it, what it contains) and the run index (which items touched it, and the
decisions about those items). Neither read starts a process or hashes a source
tree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from context_rail.context.surface import CARD_CHARS, _bounded
from context_rail.model.codegraph import Neighbors, downstream, resolve, upstream
from context_rail.store import query
from context_rail.store.codegraph import freshness, open_graph
from context_rail.store.write import WriteRefused

__all__ = ["code_view", "looks_like_code_target", "normalize_target"]

PACK_PREFIX = "pack:"
_LISTED = 10
_SOURCE_END = re.compile(
    r"\.(py|pyi|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|rb|php|c|h|cc|cpp|hpp|cs|swift|scala|sh"
    r"|md|json|ya?ml|toml)$",
    re.IGNORECASE,
)
_SYMBOL_AFTER_EXTENSION = re.compile(r"\.[A-Za-z0-9]+:[^:\s]")
_DRIVE = re.compile(r"^[A-Za-z]:/")


def looks_like_code_target(arg: str) -> bool:
    """Whether ``arg`` names a file or symbol rather than a slug or a pack target.

    A slug is lower-case letters, digits and hyphens, so a separator, a dot or
    a ``:`` after an extension can never be part of one.
    """
    if not isinstance(arg, str) or not arg.strip() or arg.startswith(PACK_PREFIX):
        return False
    return (
        "/" in arg
        or "\\" in arg
        or bool(_SYMBOL_AFTER_EXTENSION.search(arg))
        or bool(_SOURCE_END.search(arg))
    )


def normalize_target(arg: str) -> str:
    """``path`` or ``path:Symbol`` with forward slashes and no leading ``./``."""
    text = arg.strip().replace("\\", "/")
    if text.startswith("/") or _DRIVE.match(text):
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a code target is a path inside the repository, not an absolute path",
            {"target": arg},
        )
    slash = text.rfind("/") + 1
    if ":" in text[:slash]:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a code target looks like path or path:Symbol",
            {"target": arg},
        )
    path, _colon, symbol = text.partition(":")
    while path.startswith("./"):
        path = path[2:]
    if not path or any(part == ".." for part in path.split("/")):
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a code target must stay inside the repository",
            {"target": arg},
        )
    return path + (f":{symbol}" if symbol else "")


def code_view(context: Any, target: str, *, limit: int = CARD_CHARS) -> dict[str, Any]:
    """Callers, callees, contents, touching items and decisions for one target."""
    wanted = normalize_target(target)
    path, _colon, symbol = wanted.partition(":")
    store = Path(context.store)
    state = freshness(store)
    commit = state["graph"]
    graph = None if commit is None else open_graph(store, commit)
    view: dict[str, Any] = {"target": wanted, "kind": None, "graph": _graph_line(state)}
    ids: tuple[str, ...] = ()
    if graph is not None:
        with graph:
            ids = resolve(graph, wanted)
            if len(ids) > 1:
                raise WriteRefused(
                    "CONFLICT",
                    f"{wanted!r} matches {len(ids)} symbols; name one",
                    {"target": wanted, "candidates": list(ids)},
                )
            if ids:
                view.update(_neighbors(graph, ids))
    touch_symbol = ids[0] if ids and symbol else (wanted if symbol else None)
    touches = query.code_touches(context.index, path, touch_symbol, limit=_LISTED)
    items = list(dict.fromkeys(row["item"] for row in touches))
    notes = _notes(context.index, path)
    view.setdefault("callers", [])
    view.setdefault("callees", [])
    view.setdefault("contains", [])
    view["items"] = items
    view["runs"] = [row["record"] for row in touches]
    view["decisions"] = _decisions(context.index, items)
    view["notes"] = notes
    if commit is not None and not ids and not touches and not notes:
        raise LookupError("not_found")
    return _bounded(view, limit)


def _graph_line(state: dict[str, Any]) -> str:
    commit, head = state["graph"], state["head"]
    if commit is None:
        return "none"
    if state["state"] == "fresh":
        return f"fresh @{commit[:7]}"
    return f"stale @{commit[:7]}" + ("" if head is None else f" (HEAD {head[:7]})")


def _neighbors(graph: Neighbors, ids: tuple[str, ...]) -> dict[str, Any]:
    node = graph.node(ids[0])
    contained = (
        []
        if node is None
        else [
            inner.id
            for inner in graph.by_file(node.file)
            if node.kind == "file" and inner.kind != "file"
        ]
    )
    return {
        "kind": None if node is None else node.kind,
        "callers": list(upstream(graph, ids, depth=1, limit=_LISTED)),
        "callees": list(downstream(graph, ids, depth=1, limit=_LISTED)),
        "contains": contained[:_LISTED],
    }


def _notes(index: Any, path: str) -> list[str]:
    rows = index.conn.execute(
        "SELECT d.slug FROM docs d JOIN fields f ON f.path = d.path "
        "WHERE d.preset = 'code' AND d.broken = 0 "
        "AND f.key = 'source_path' AND f.value_json = ? ORDER BY d.slug LIMIT ?",
        (json.dumps(path), _LISTED),
    ).fetchall()
    return [row[0] for row in rows]


def _decisions(index: Any, items: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in items:
        for record in query.records_about(index, item, limit=20):
            if record["kind"] == "decision.made":
                reason = (record["data"] or {}).get("reason")
                found.append({"id": record["id"], "item": item, "reason": reason})
    return found[:_LISTED]
