"""The legacy-shaped project snapshot, rebuilt from the pack and the index.

The React UI (copied from context-rail-link) renders one snapshot document:
tasks, phases, scope items, decisions, risks, questions and the cursor. This
module keeps that contract and adds the facts the new engine knows: the pack,
each item's workflow, the pack-wide status categories and the workflow quality
rules. Nothing here writes, runs a command or hashes a source tree.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_rail.kernel import structure
from context_rail.model.pack import Pack

__all__ = ["build", "item_snapshot"]

_DECISION = "decision"
_RISK = "risk"
_QUESTION = "question"
_MILESTONE = "milestone"
_CORE = ("goal", "vision", "scope", "wont", "charter", "convention")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _doc_rows(index: Any) -> list[tuple[Any, ...]]:
    return index.conn.execute(
        "SELECT path, slug, primitive, preset, workflow, title, status, category, "
        "created, updated, etag FROM docs WHERE broken = 0 "
        "ORDER BY updated DESC, slug ASC, path ASC"
    ).fetchall()


def _fields(index: Any) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path, key, value in index.conn.execute(
        "SELECT path, key, value_json FROM fields ORDER BY path, rowid"
    ):
        found.setdefault(path, {})[key] = json.loads(value)
    return found


def _relations(index: Any) -> dict[str, dict[str, list[str]]]:
    found: dict[str, dict[str, list[str]]] = {}
    for src, rel, dst in index.conn.execute(
        "SELECT src_path, rel, dst FROM relations ORDER BY src_path, rel, dst"
    ):
        found.setdefault(src, {}).setdefault(rel, []).append(dst)
    return found


def _container(pack: Pack, preset: str | None, slug: str, parents: set[str]) -> bool:
    declared = pack.presets.get(preset or "")
    if declared is not None and declared.container is not None:
        return declared.container
    return slug in parents


def _priority(preset: Any) -> bool:
    return "priority" in (preset.fields if preset is not None else {})


def _workflows(pack: Pack) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in sorted(pack.workflows):
        workflow = pack.workflows[name]
        out.append(
            {
                "name": name,
                "initial": workflow.initial,
                "states": list(workflow.transitions),
                "transitions": {
                    state: list(targets)
                    for state, targets in workflow.transitions.items()
                },
                "quality": {
                    state: {"gate": rule.gate, "checks": list(rule.checks)}
                    for state, rule in workflow.quality.items()
                },
                "presets": sorted(
                    preset.name
                    for preset in pack.presets.values()
                    if preset.workflow == name
                ),
            }
        )
    return out


def _presets(pack: Pack) -> list[dict[str, Any]]:
    return [
        {
            "name": preset.name,
            "primitive": preset.primitive,
            "workflow": preset.workflow,
            "container": preset.container,
            "fields": {key: list(values) for key, values in preset.fields.items()},
            "required": list(preset.required),
        }
        for preset in pack.presets.values()
    ]


def _core_docs(
    pack: Pack, docs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    wanted = [
        name
        for name in _CORE
        if name in pack.presets and pack.presets[name].primitive != "item"
    ]
    return [doc for doc in docs if doc["preset"] in wanted]


def _project(context: Any, docs: list[dict[str, Any]], counts: dict[str, int]) -> dict[str, Any]:
    from context_rail.store.approval import approval_state

    state = approval_state(context, "charter")
    goal = next((doc for doc in docs if doc["preset"] == "goal"), None)
    summary = ", ".join(
        f"{name} {count}" for name, count in counts.items() if count
    ) or "No items yet"
    name = Path(context.store).name
    if name == "context-rail":
        name = Path(context.store).parent.name
    return {
        "name": name,
        "store_root": str(Path(context.store).absolute()),
        "pack": context.pack.name,
        "state": state.get("state"),
        "current_phase": None,
        "status_summary": summary,
        "goal": goal["title"] if goal else None,
        "ambiguous_doc_ids": [],
    }


def build(context: Any) -> dict[str, Any]:
    """One pack-aware snapshot document for the React shell."""
    index = context.index
    pack = context.pack
    fields = _fields(index)
    relations = _relations(index)
    rows = _doc_rows(index)
    parents = {
        dst
        for by_rel in relations.values()
        for dst in by_rel.get("part_of", ())
    }
    container_by_slug = {
        slug: _container(pack, preset, slug, parents)
        for _path, slug, _primitive, preset, _workflow, _title, _status, _category, _created, _updated, _etag in rows
    }
    docs: list[dict[str, Any]] = []
    for (
        path,
        slug,
        primitive,
        preset,
        workflow,
        title,
        status,
        category,
        created,
        updated,
        etag,
    ) in rows:
        values = fields.get(path, {})
        item_container = container_by_slug[slug]
        part_of = relations.get(path, {}).get("part_of", [])
        phase = next(
            (parent for parent in part_of if container_by_slug.get(parent)),
            part_of[0] if part_of else None,
        )
        doc = {
            "id": slug,
            "title": title or slug,
            "type": preset or primitive,
            "preset": preset,
            "primitive": primitive,
            "workflow": workflow,
            "status": status,
            "category": category,
            "path": path,
            "created": created,
            "updated": updated,
            "container": item_container,
            "phase": phase,
            "etag": etag,
            "fields": values,
            "relations": relations.get(path, {}),
            "priority": values.get("priority"),
            "states": list(
                (
                    pack.workflows.get(workflow or "default")
                    or pack.workflow("default")
                ).transitions
            ),
        }
        docs.append(doc)

    by_preset: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        by_preset.setdefault(doc["preset"] or "", []).append(doc)

    items = [doc for doc in docs if doc["primitive"] == "item"]
    phases = [
        doc
        for doc in items
        if doc["container"] and not _priority(pack.presets.get(doc["preset"] or ""))
    ]
    tasks = [doc for doc in items if not doc["container"]]
    children: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if task["phase"]:
            children.setdefault(task["phase"], []).append(task)
    for phase in phases:
        kids = children.get(phase["id"], [])
        categories = {kid["category"] for kid in kids}
        phase["children"] = len(kids)
        phase["state"] = (
            "done"
            if kids and categories <= {"done", "dropped"}
            else "active"
            if categories & {"active", "blocked"}
            else "planned"
        )
        phase["description"] = (
            phase["fields"].get("description")
            or phase["fields"].get("summary")
            or ""
        )
    scope_items = [
        doc
        for doc in items
        if _priority(pack.presets.get(doc["preset"] or ""))
    ]
    if not scope_items and "scope" in pack.presets:
        scope_items = by_preset.get("scope", [])

    counts: dict[str, int] = {}
    for doc in items:
        name = doc["category"] or "unknown"
        counts[name] = counts.get(name, 0) + 1

    def titles(predicate: Any) -> str:
        found = [doc["title"] for doc in items if predicate(doc)]
        return " · ".join(found[:8])

    cursor = {
        "in_progress": titles(lambda doc: doc["category"] == "active"),
        "blocked": titles(lambda doc: doc["category"] == "blocked"),
        "next": " · ".join(
            doc["title"] for doc in tasks if doc["category"] in ("not_started", "active")
        )[:400],
        "completed": titles(lambda doc: doc["category"] == "done"),
        "updated": _now(),
    }

    return {
        "snapshot_version": 1,
        "schema": 4,
        "pack": pack.name,
        "project": _project(context, docs, counts),
        "statuses": list(pack.statuses),
        "status_categories": dict(pack.statuses),
        "workflows": _workflows(pack),
        "presets": _presets(pack),
        "core_docs": _core_docs(pack, docs),
        "tasks": tasks,
        "phases": phases,
        "milestones": by_preset.get(_MILESTONE, []),
        "scope_items": scope_items,
        "decisions": by_preset.get(_DECISION, []),
        "risks": by_preset.get(_RISK, []),
        "questions": by_preset.get(_QUESTION, []),
        "cursor": cursor,
        "claims": [],
        "counts": counts,
    }


def item_snapshot(context: Any, slug: str) -> dict[str, Any] | None:
    """One item in the snapshot's shape, for the document drawer."""
    snapshot = build(context)
    for doc in snapshot["tasks"] + snapshot["phases"]:
        if doc["id"] == slug:
            return doc
    return None
