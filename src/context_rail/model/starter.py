"""Starter roles: a role block generated from a pack's own presets and workflows.

The result is plain pack data in the declared spelling, so ``parse_roles``
accepts it and a human can edit it before approving. Nothing here reads a
preset's name to decide anything: every list comes from a preset's primitive,
its container flag, its workflow or the pack's hierarchy.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from context_rail.model.fields import refuse
from context_rail.model.metadata import ENGINE_PRESETS
from context_rail.model.primitives import RELATIONS
from context_rail.model.roles import workflow_statuses

if TYPE_CHECKING:
    from context_rail.model.pack import Pack

__all__ = ["starter_roles"]

_CODE_READ = {"read": ["**"], "write": []}
_LEAD_PARTS = ["title", "status", "next", "blockers", "waits"]
_LEVEL_PARTS = [
    "title",
    "status",
    "next",
    "body",
    "acceptance",
    "blockers",
    "runs",
    "records",
]
_IMPLEMENTER_PARTS = ["title", "body", "acceptance", "code_area", "records"]
_REVIEWER_PARTS = ["title", "acceptance", "diff", "evidence", "code_area"]


def starter_roles(pack: Pack) -> dict[str, Any]:
    """A lead, one lead per hierarchy level, an implementer and a reviewer."""
    items = [
        name for name, preset in pack.presets.items() if preset.primitive == "item"
    ]
    leaves = [name for name in items if not pack.presets[name].container]
    levels = (
        []
        if pack.operating is None
        else [level.preset for level in pack.operating.hierarchy]
    )
    shared = [
        name
        for name, preset in pack.presets.items()
        if preset.primitive in ("note", "record") and name not in ENGINE_PRESETS
    ]
    notes = [name for name in shared if pack.presets[name].primitive == "note"]
    workers = ["implementer", "reviewer"] if leaves else []
    level_roles = {preset: f"{preset.replace('_', '-')}-lead" for preset in levels}
    if len(set(level_roles.values())) != len(level_roles):
        refuse("two hierarchy presets make the same level-lead role name")

    roles: dict[str, Any] = {
        "lead": _lead(pack, items, leaves, [*level_roles.values(), *workers])
    }
    for preset, name in level_roles.items():
        roles[name] = _level_lead(pack, preset, leaves, shared, workers)
    if leaves:
        roles["implementer"] = _implementer(leaves, notes)
        roles["reviewer"] = _reviewer(leaves, notes)
    # Round-tripping through JSON gives every role its own lists, so a dumped
    # YAML draft has no anchors or aliases.
    return json.loads(json.dumps(roles))


def _statuses(pack: Pack, preset: str) -> list[str]:
    return list(workflow_statuses(pack.workflow_for(preset)))


def _lead(
    pack: Pack, items: list[str], leaves: list[str], grants: list[str]
) -> dict[str, Any]:
    return {
        "anchor": "project",
        "reach": {"read": ["project"], "write": ["project"], "code": dict(_CODE_READ)},
        "can": {
            "add": items,
            "update": items,
            "move": {name: _statuses(pack, name) for name in items},
            "link": list(RELATIONS),
            "rename": items,
            "relocate": True,
            "check": items,
            "verify": items,
            "report": leaves,
            "triage": leaves,
            "execution": leaves,
            "actions": list(pack.actions),
            "grant": grants,
        },
        "sees": {
            "up": 0,
            "down": 1,
            "parts": _LEAD_PARTS,
            "budget": {"chars": 12000, "docs": 60},
        },
        "workspace": "none",
    }


def _level_lead(
    pack: Pack, preset: str, leaves: list[str], shared: list[str], grants: list[str]
) -> dict[str, Any]:
    read: list[Any] = ["anchor", {"up": 1}]
    if shared:
        read.append({"preset": shared})
    moves = {name: _statuses(pack, name) for name in [preset, *leaves]}
    return {
        "anchor": {"level": preset},
        "reach": {"read": read, "write": ["anchor"], "code": dict(_CODE_READ)},
        "can": {
            "add": leaves,
            "update": [preset, *leaves],
            "move": moves,
            "link": ["part_of", "depends_on"],
            "rename": leaves,
            "check": leaves,
            "verify": leaves,
            "report": leaves,
            "triage": leaves,
            "execution": leaves,
            "grant": grants,
        },
        "sees": {
            "up": 1,
            "down": "all",
            "relations": ["depends_on"],
            "parts": _LEVEL_PARTS,
            "budget": {"chars": 8000, "docs": 40},
        },
        "workspace": "none",
    }


def _worker_read(notes: list[str]) -> list[Any]:
    read: list[Any] = ["anchor", {"up": 1}, {"related": ["depends_on"]}]
    if notes:
        read.append({"preset": notes})
    return read


def _implementer(leaves: list[str], notes: list[str]) -> dict[str, Any]:
    return {
        "anchor": {"item": leaves},
        "reach": {
            "read": _worker_read(notes),
            "write": ["anchor"],
            "code": {"read": ["**"], "write": ["**"]},
        },
        "can": {"candidate": leaves, "report": leaves},
        "sees": {
            "up": 1,
            "relations": ["depends_on"],
            "parts": _IMPLEMENTER_PARTS,
            "budget": {"chars": 16000, "docs": 20},
        },
        "workspace": "write",
    }


def _reviewer(leaves: list[str], notes: list[str]) -> dict[str, Any]:
    return {
        "anchor": {"item": leaves},
        "reach": {
            "read": _worker_read(notes),
            "write": ["anchor"],
            "code": dict(_CODE_READ),
        },
        "can": {"review": leaves, "report": leaves},
        "sees": {
            "up": 1,
            "relations": ["depends_on"],
            "parts": _REVIEWER_PARTS,
            "budget": {"chars": 16000, "docs": 20},
        },
        "workspace": "read",
        "independent_of": ["produce"],
    }
