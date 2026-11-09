"""Compile a pack into the bounded views an agent reads.

The surface is a projection, not a new source of truth: it reads the pack, the
manifest and the index, and never writes. Its four views answer what kinds of
work exist, where one item is, what the full definition is, and which verbs the
pack offers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from context_rail.kernel.lifecycle import category_of
from context_rail.store import query

__all__ = [
    "CARD_CHARS",
    "CATALOG_CHARS",
    "EXPLAIN_CHARS",
    "VERBS_CHARS",
    "SurfaceError",
    "card",
    "catalog",
    "explain",
    "explain_workflow",
    "render_card",
    "render_workflows",
    "verbs",
    "why",
]

CATALOG_CHARS = 4000
CARD_CHARS = 1500
EXPLAIN_CHARS = 8000
VERBS_CHARS = 2000


class SurfaceError(Exception):
    """A surface request the pack cannot answer."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def catalog(
    context: Any,
    role: str = "cto",
    *,
    item: str | None = None,
    limit: int = CATALOG_CHARS,
) -> dict[str, Any]:
    """Every document type the pack declares, with its workflow and use line.

    A CTO sees the whole catalog. A worker sees only the type of the item it was
    given, so a narrow order never exposes the rest of the project.
    """
    pack = context.pack
    names = list(pack.presets)
    if role != "cto":
        if item is None:
            return {"items": [], "omitted": 0}
        doc = _document(context, item)
        preset = pack.presets.get(doc["preset"] or "")
        if preset is None:
            raise SurfaceError(
                "VALIDATION_FAILED",
                f"unknown preset {doc['preset']!r}",
                {"slug": item},
            )
        names = [preset.name]
    entries: list[dict[str, Any]] = []
    used = 0
    for name in names:
        preset = pack.presets[name]
        entry = {
            "name": preset.name,
            "primitive": preset.primitive,
            "use_when": _use_when(preset),
            "workflow": preset.workflow,
            "container": preset.container,
            "states": list(pack.workflow(preset.workflow).transitions),
        }
        size = len(json.dumps(entry, sort_keys=True))
        if used + size > limit and entries:
            break
        entries.append(entry)
        used += size
    return {"items": entries, "omitted": len(names) - len(entries)}


def card(context: Any, slug: str, *, limit: int = CARD_CHARS) -> dict[str, Any]:
    """One item's workflow, current state, next states and proof bar."""
    pack = context.pack
    doc = _document(context, slug)
    preset = pack.presets.get(doc["preset"] or "")
    if preset is None:
        raise SurfaceError(
            "VALIDATION_FAILED",
            f"unknown preset {doc['preset']!r}",
            {"slug": slug},
        )
    workflow = pack.workflow_for(preset.name)
    status = doc["status"] or ""
    next_states = sorted(workflow.transitions.get(status, ()))
    gate = _gate(context, workflow, doc, status)
    proof = _proof(context, workflow, doc, status, gate)
    payload: dict[str, Any] = {
        "slug": doc["slug"],
        "title": doc["title"] or doc["slug"],
        "preset": preset.name,
        "workflow": workflow.name,
        "state": status,
        "next": next_states,
        "states": sorted(workflow.transitions),
        "container": preset.container,
        "gate": gate,
        "proof": proof,
        "command": _command(doc["slug"], next_states),
    }
    payload.update(_operating_card(context, preset.name, doc["slug"]))
    return _bounded(payload, limit)


def explain(
    context: Any, name: str, *, limit: int = EXPLAIN_CHARS
) -> dict[str, Any]:
    """The full definition of one preset or workflow."""
    pack = context.pack
    preset = pack.presets.get(name)
    if preset is not None:
        workflow = pack.workflow(preset.workflow)
        payload: dict[str, Any] = {
            "kind": "preset",
            "name": preset.name,
            "primitive": preset.primitive,
            "workflow": workflow.name,
            "container": preset.container,
            "tags": list(preset.tags),
            "fields": {key: list(values) for key, values in preset.fields.items()},
            "required": list(preset.required),
            "defaults": dict(preset.defaults),
            "states": list(workflow.transitions),
        }
        payload.update(_operating_note(context, preset.name))
        return _bounded(payload, limit)
    workflow = pack.workflows.get(name)
    if workflow is not None:
        return explain_workflow(context, name, limit=limit)
    action = pack.actions.get(name)
    if action is not None:
        return _bounded(
            {
                "kind": "action",
                "name": action.name,
                "on": action.on,
                "from": list(action.from_states),
                "tool": action.tool,
                "args": dict(action.args),
                "use_when": action.use_when,
                "version": action.version,
            },
            limit,
        )
    raise SurfaceError(
        "NOT_FOUND",
        f"{name!r} is neither a preset nor a workflow",
        {"name": name},
    )


def explain_workflow(
    context: Any, name: str, *, limit: int = EXPLAIN_CHARS
) -> dict[str, Any]:
    """One workflow by name, even when a preset shares that name."""
    workflow = context.pack.workflows.get(name)
    if workflow is None:
        raise SurfaceError(
            "NOT_FOUND", f"no workflow {name!r}", {"name": name}
        )
    payload = {
        "kind": "workflow",
        "name": workflow.name,
        "initial": workflow.initial,
        "states": list(workflow.transitions),
        "transitions": {
            source: list(targets)
            for source, targets in workflow.transitions.items()
        },
        "quality": {
            status: {"gate": rule.gate, "checks": list(rule.checks)}
            for status, rule in workflow.quality.items()
        },
    }
    payload.update(_operating_note(context, name))
    return _bounded(payload, limit)


def render_card(card: Mapping[str, Any], *, limit: int = CARD_CHARS) -> str:
    """One card as the short block a work order carries."""
    proof = card.get("proof") or {}
    checks = ", ".join(proof.get("checks") or ()) or "(none)"
    lines = [
        f"workflow: {card['workflow']}",
        f"state: {card['state']}",
        f"next: {', '.join(card.get('next') or ()) or '(none)'}",
        f"gate: {card.get('gate')}",
        f"proof: checks {checks}"
        + (", acceptance" if proof.get("acceptance") else "")
        + (", review" if proof.get("review") else "")
        + (", human" if proof.get("human") else "")
        + (f", children {proof['children']}" if proof.get("children") else ""),
        f"command: {card.get('command')}",
    ]
    focus = card.get("focus")
    if isinstance(focus, Mapping):
        lines.insert(0, f"focus: {focus.get('title') or focus.get('slug')}")
    actions = card.get("actions") or ()
    if actions:
        lines.append("actions: " + ", ".join(actions))
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 12] + "\n[truncated]"


def render_workflows(context: Any, *, limit: int = 2000) -> str:
    """The workflow shapes a CTO order needs, without a single item's card."""
    lines: list[str] = []
    for name in sorted(context.pack.workflows):
        workflow = context.pack.workflows[name]
        gates = ", ".join(
            f"{status}->{rule.gate}"
            for status, rule in sorted(workflow.quality.items())
        ) or "(done uses item_done)"
        lines.append(
            f"{name}: {workflow.initial} -> "
            f"{', '.join(sorted(workflow.transitions))}; gates {gates}"
        )
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 12] + "\n[truncated]"


def why(context: Any, slug: str, *, limit: int = CARD_CHARS) -> dict[str, Any]:
    """The missing proof for one item, or the reach of one file or symbol."""
    from context_rail.context import code_targets

    if code_targets.looks_like_code_target(slug):
        return code_targets.code_view(context, slug, limit=limit)
    view = card(context, slug, limit=limit)
    rows = query.check_states(context.index, slug)
    missing = [row["slug"] for row in rows if not row.get("passed")]
    return {
        "slug": view["slug"],
        "workflow": view["workflow"],
        "state": view["state"],
        "gate": view["gate"],
        "proof": view["proof"],
        "missing": missing,
        "command": view["command"],
    }


def verbs(context: Any) -> dict[str, Any]:
    """The pack's declarative actions, once actions are declared."""
    actions = getattr(context.pack, "actions", {}) or {}
    entries = [
        {
            "name": action.name,
            "on": action.on,
            "from": list(action.from_states),
            "use_when": action.use_when,
        }
        for action in actions.values()
    ]
    payload = {"items": entries, "omitted": 0}
    return _bounded(payload, VERBS_CHARS)


def _compiled(context: Any) -> Any:
    model = getattr(context, "model", None)
    if model is not None:
        return model
    from context_rail.model.compiler import compile_project

    return compile_project(context.pack, context.pack.operating)


def _operating_card(context: Any, preset: str, slug: str) -> dict[str, Any]:
    from context_rail.context import operating

    model = _compiled(context)
    row = operating.item_row(context, slug)
    actions = (
        ()
        if row is None
        else operating.applicable_actions(
            context, model, row, getattr(context, "actor", "cto")
        )
    )
    policy = model.operating.focus
    return {
        "preset_label": operating.label(model, preset),
        "unit": model.operating.unit,
        "focus": operating.focus_membership(context, model, slug),
        "actions": list(actions),
        "operating": {
            "role": operating.role_for(model, preset),
            "hierarchy": [level.preset for level in model.operating.hierarchy],
            "focus": None if policy is None else policy.preset,
        },
    }


def _operating_note(context: Any, name: str) -> dict[str, Any]:
    from context_rail.context import operating

    model = _compiled(context)
    policy = model.operating.focus
    return {
        "operating": {
            "role": operating.role_for(model, name),
            "unit": model.operating.unit,
            "hierarchy": [level.preset for level in model.operating.hierarchy],
            "queue": list(model.operating.queue.presets),
            "progress": list(model.operating.progress.of),
            "focus": None if policy is None else policy.preset,
        }
    }


def _document(context: Any, slug: str) -> dict[str, Any]:
    rows = context.index.conn.execute(
        "SELECT slug, preset, title, status, path FROM docs "
        "WHERE slug = ? ORDER BY path",
        (slug,),
    ).fetchall()
    if not rows:
        raise SurfaceError("NOT_FOUND", f"no item {slug!r}", {"slug": slug})
    if len(rows) > 1:
        raise SurfaceError(
            "CONFLICT", f"{slug!r} names more than one document", {"slug": slug}
        )
    row = rows[0]
    return {
        "slug": row[0],
        "preset": row[1],
        "title": row[2],
        "status": row[3],
        "path": row[4],
    }


def _gate(context: Any, workflow: Any, doc: Mapping[str, Any], status: str) -> str:
    rule = workflow.gate_for(status)
    if rule is not None:
        return rule
    if category_of(context.pack, status) != "done":
        return "none"
    policy = context.policy
    if policy is None:
        return "none"
    if doc["slug"] in (policy.project.get("scope") or ()):
        return "scope_done"
    return "item_done"


def _proof(
    context: Any,
    workflow: Any,
    doc: Mapping[str, Any],
    status: str,
    gate: str,
) -> dict[str, Any]:
    policy = context.policy
    rule = policy.gates.get(gate) if policy is not None else None
    selected = workflow.checks_for(status)
    rows = query.check_states(context.index, doc["slug"])
    names = (
        list(selected)
        if selected is not None
        else [row["slug"] for row in rows]
    )
    return {
        "gate": gate,
        "checks": names,
        "acceptance": bool(rule and rule.require_acceptance),
        "review": bool(rule and rule.require_review),
        "human": bool(rule and rule.require_human),
        "children": "done or dropped" if _is_container(context, doc) else None,
    }


def _is_container(context: Any, doc: Mapping[str, Any]) -> bool:
    preset = context.pack.presets.get(doc["preset"] or "")
    return bool(preset is not None and preset.container)


def _command(slug: str, next_states: Sequence[str]) -> str:
    if not next_states:
        return f"rail read show {slug}"
    return f"rail change move {slug} {next_states[0]} --expect <etag>"


def _use_when(preset: Any) -> str:
    if preset.container:
        return f"group work under a {preset.name}"
    if preset.primitive == "item":
        return f"file or work on a {preset.name}"
    return f"record a {preset.name}"


def _bounded(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    """Trim list fields until the JSON fits, and name what was left out."""
    if len(json.dumps(payload, sort_keys=True)) <= limit:
        payload.setdefault("omitted", 0)
        return payload
    omitted = 0
    for key in list(payload):
        value = payload[key]
        if not isinstance(value, list):
            continue
        while value and len(json.dumps(payload, sort_keys=True)) > limit:
            value.pop()
            omitted += 1
    payload["omitted"] = omitted
    return payload
