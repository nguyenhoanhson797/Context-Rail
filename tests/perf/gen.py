"""Deterministic store generator shared by the index, query and budget tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from context_rail.core.util import dump_frontmatter
from context_rail.model.manifest import DEFAULT_MANIFEST, initial_manifest

STAMP = "2026-10-28T00:00:00Z"
STATUSES = ("todo", "doing", "done")

__all__ = ["make_store"]


def make_store(
    root: Path, n: int, seed: int = 0, *, roles: bool = False, with_results: bool = False
) -> Path:
    """Write ``n`` valid documents under ``root/context-rail`` and return it.

    ``roles=True`` adds a local role-mode pack and two live seats: a project
    lead and one level lead on the first goal. The seats are written as
    records, so opening the index folds them like any other store.

    ``with_results=True`` uses the shipped ``basic`` pack and writes valid
    Markdown producer result records for done tasks; ``with_results=False``
    writes a minimal local ``basic`` pack without ``results``.

    The store carries no charter, so it is locked out: a test that moves work
    approves it first with :func:`approve_generated`.
    """
    if n < 0:
        raise ValueError(f"a store holds a non-negative number of docs, not {n}")
    store = Path(root) / "context-rail"
    store.mkdir(parents=True, exist_ok=True)
    if with_results:
        _, manifest_data = initial_manifest("basic", store)
    else:
        manifest_data = DEFAULT_MANIFEST
        _write_contractless_basic(store)
    (store / "rail.yaml").write_text(
        yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8", newline="\n"
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
        task_status = STATUSES[i % 3]
        _write(
            store,
            f"items/task/t{i}.md",
            "item",
            "task",
            f"Task {i}",
            seed,
            "task",
            i,
            status=task_status,
            tags=["task"],
            relations=relations,
        )
        if with_results and task_status == "done":
            _write_result(store, f"t{i}", i)
    if roles:
        _write_roles(store)
    return store


def _write_contractless_basic(store: Path) -> None:
    """Install a minimal local ``basic`` pack without ``results``."""
    import context_rail

    local = store / "packs" / "basic"
    local.mkdir(parents=True, exist_ok=True)
    pack_path = Path(context_rail.__file__).parent / "packs" / "basic" / "pack.yaml"
    document = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    document.pop("results", None)
    (local / "pack.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _write_result(store: Path, slug: str, index: int) -> None:
    from context_rail.model.compiler import load_project
    from context_rail.model.results import RESULT_KIND, result_body_hash
    from context_rail.store.doc import record_digest
    from context_rail.store.results import item_content_digest

    body = (
        "## Summary\nDone.\n\n## Findings\nNone.\n\n## Decisions\nNone.\n\n"
        "## Evidence\nVerified.\n\n## Next steps\nNone.\n"
    )
    body_hash = result_body_hash(body)
    record_id = f"01JGENRES{index:017d}"
    item_digest = item_content_digest(store, f"items/task/{slug}.md")
    compiled = load_project("basic", store).results.get("default")
    contract_digest = compiled.produce.digest if compiled is not None else "0" * 64
    payload = {
        "schema": 1,
        "item": slug,
        "attempt": 1,
        "actor": "cto",
        "at": STAMP,
        "outcome": "completed",
        "candidate_commit": None,
        "body_hash": body_hash,
        "item_digest": item_digest,
        "work_cycle": None,
        "contract_digest": contract_digest,
    }
    front: dict[str, Any] = {
        "primitive": "record",
        "preset": "result",
        "title": f"{slug} result 1",
        "tags": ["result"],
        "id": record_id,
        "kind": RESULT_KIND,
        "at": STAMP,
        "actor": "cto",
        "about": [f"[[{slug}]]"],
        "data": payload,
        "workflow_name": "default",
    }
    front["digest"] = record_digest(front)
    path = store / "records" / "results" / "default" / slug / f"{record_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )


def approve_generated(store: Path) -> None:
    """Approve a generated store so its documents may move.

    Every gate is off: this unlocks the store, it does not set a quality bar.
    """
    from helpers import approve_store

    approve_store(store, goal="g0")


def _write_roles(store: Path) -> None:
    """Turn the generated store into role mode and grant its two seats."""
    import context_rail
    from context_rail.model.pack import load_pack
    from context_rail.model.starter import starter_roles

    local = store / "packs" / "basic"
    if not local.exists():
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
