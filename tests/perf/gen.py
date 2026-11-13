"""Deterministic store generator shared by the index, query and budget tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from context_rail.core.util import dump_frontmatter
from context_rail.model.manifest import DEFAULT_MANIFEST

STAMP = "2026-10-28T00:00:00Z"
STATUSES = ("todo", "doing", "done")

__all__ = ["make_store"]


def make_store(root: Path, n: int, seed: int = 0, *, roles: bool = False) -> Path:
    """Write ``n`` valid documents under ``root/context-rail`` and return it.

    ``roles=True`` adds a local role-mode pack and two live seats: a project
    lead and one level lead on the first goal. The seats are written as
    records, so opening the index folds them like any other store.

    The store carries no charter, so it is locked out: a test that moves work
    approves it first with :func:`approve_generated`.
    """
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
            status="doing",
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
    if roles:
        _write_roles(store)
    return store


def approve_generated(store: Path) -> None:
    """Approve a generated store so its documents may move.

    Every gate is off: this unlocks the store, it does not set a quality bar.
    """
    from context_rail.gateway.gateway import invoke

    from helpers import approve_picture

    gates = {
        name: {
            "require_checks": False,
            "require_acceptance": False,
            "require_review": False,
            "require_human": False,
        }
        for name in ("item_done", "scope_done", "merge", "release")
    }
    fields = {
        "primitive": "policy",
        "preset": "charter",
        "title": "Charter",
        "tags": [],
        "schema": 1,
        "project": {"goal": "g0", "vision": "vision", "scope": [], "wont": []},
        "protected_files": ["rail.yaml", "charter.md"],
        "default_branch": "main",
        "gates": gates,
    }
    (store / "charter.md").write_text(
        "---\n" + dump_frontmatter(fields) + "---\n# Charter\n",
        encoding="utf-8",
        newline="\n",
    )
    opened = invoke("reindex", {}, store=store, actor="human")
    if not opened["ok"]:
        raise AssertionError(opened)
    approved = approve_picture(store)
    if not approved["ok"]:
        raise AssertionError(approved)


def _write_roles(store: Path) -> None:
    """Turn the generated store into role mode and grant its two seats."""
    import context_rail
    from context_rail.model.pack import load_pack
    from context_rail.model.starter import starter_roles

    local = store / "packs" / "basic"
    shutil.copytree(Path(context_rail.__file__).parent / "packs" / "basic", local)
    pack = load_pack("basic", store)
    document = yaml.safe_load((local / "pack.yaml").read_text(encoding="utf-8"))
    document["operating"]["version"] = 2
    document["operating"]["roles"] = starter_roles(pack)
    (local / "pack.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    _grant(store, "seat-lead-1", "lead-1", "lead", "project", "human", "00:00:01")
    _grant(
        store, "seat-goal-1", "goal-1", "goal-lead", "g0", "lead-1", "00:00:02"
    )


def _grant(
    store: Path,
    record_id: str,
    seat: str,
    role: str,
    anchor: str,
    granted_by: str,
    clock: str,
) -> None:
    from context_rail.model.seats import SEAT_GRANTED

    from helpers import write_record

    write_record(
        store,
        record_id,
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": seat,
            "role": role,
            "anchor": anchor,
            "granted_by": granted_by,
        },
        at=f"2026-10-28T{clock}Z",
        actor=granted_by,
        about=(),
    )



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
