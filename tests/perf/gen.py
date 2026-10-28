"""Deterministic store generator shared by the index, query and budget tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_rail.core.util import dump_frontmatter
from context_rail.model.manifest import DEFAULT_MANIFEST

STAMP = "2026-10-28T00:00:00Z"
STATUSES = ("todo", "doing", "done")

__all__ = ["make_store"]


def make_store(root: Path, n: int, seed: int = 0) -> Path:
    """Write ``n`` valid documents under ``root/context-rail`` and return it."""
    if n < 0:
        raise ValueError(f"a store holds a non-negative number of docs, not {n}")
    store = Path(root) / "context-rail"
    store.mkdir(parents=True, exist_ok=True)
    (store / "rail.yaml").write_text(
        yaml.safe_dump(DEFAULT_MANIFEST, sort_keys=False), encoding="utf-8", newline="\n"
    )
    goals = min(n, max(1, n // 10)) if n else 0
    notes = min(n - goals, n // 5)
    tasks = n - goals - notes
    for i in range(goals):
        _write(
            store,
            f"project/g{i}.md",
            "item",
            "goal",
            f"Goal {i}",
            seed,
            "goal",
            i,
            status=STATUSES[i % 3],
            tags=["goal"],
        )
    for i in range(notes):
        _write(
            store,
            f"notes/note/n{i}.md",
            "note",
            "note",
            f"Note {i}",
            seed,
            "note",
            i,
            status=None,
            tags=[],
        )
    for i in range(tasks):
        relations = {"part_of": f"g{i % goals}"}
        if i > 0 and i % 5 == 0:
            relations["depends_on"] = f"t{i - 1}"
        _write(
            store,
            f"items/task/t{i}.md",
            "item",
            "task",
            f"Task {i}",
            seed,
            "task",
            i,
            status=STATUSES[i % 3],
            tags=["task"],
            relations=relations,
        )
    return store


def _write(
    store: Path,
    rel: str,
    primitive: str,
    preset: str,
    title: str,
    seed: int,
    kind: str,
    index: int,
    *,
    status: str | None,
    tags: list[str],
    relations: dict[str, str] | None = None,
) -> None:
    front: dict = {"primitive": primitive, "preset": preset, "title": title}
    if status is not None:
        front["status"] = status
    front["tags"] = tags
    front["created"] = STAMP
    front["updated"] = STAMP
    for name, slug in (relations or {}).items():
        front[name] = [f"[[{slug}]]"]
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + _body(title, kind, index, seed),
        encoding="utf-8",
        newline="\n",
    )


def _body(title: str, kind: str, index: int, seed: int) -> str:
    start = (seed * 37 + index * 11) % 1000
    words = " ".join(f"{kind}{index}-{start + step}" for step in range(20))
    return f"# {title}\n\n{title} is {kind} number {index}.\n\n{words}\n"
