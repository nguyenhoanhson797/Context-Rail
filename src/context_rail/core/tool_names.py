"""The grouped command surface: pure names, no imports and no I/O.

Canonical tool names stay in :mod:`context_rail.gateway.tools` for stored
profiles, MCP clients and recorded history. This module names the smaller
surface the CLI and UI present, plus compatibility aliases for the flat names.
"""

from __future__ import annotations

from typing import Iterable

__all__ = [
    "ALIASES",
    "ALIAS_DEFAULTS",
    "ALIAS_SUMMARIES",
    "COMPAT_ALIASES",
    "GROUP_SUMMARIES",
    "LEGACY_TO_SURFACE",
    "VISIBLE_ALIASES",
    "VISIBLE_PATHS",
    "alias_defaults",
    "canonical_methods",
    "canonical_tool",
]

#: The visible daily surface. Each alias points at one canonical tool.
VISIBLE_ALIASES: dict[str, str] = {
    "overview": "cto brief",
    "read status": "status",
    "read digest": "digest",
    "read find": "find",
    "read show": "show",
    "read ledger": "ledger",
    "change add": "add",
    "change update": "update",
    "change move": "move",
    "change link": "link",
    "change rename": "rename",
    "change repath": "relocate",
    "check declare": "check",
    "check run": "verify",
    "loop dispatch": "dispatch",
    "loop review": "review",
    "loop merge": "merge",
    "loop report": "report",
    "loop triage": "triage",
    "loop execution": "execution record",
    "loop tick": "cto tick",
    "gate": "ci",
    "ops doctor": "doctor",
    "ops reindex": "reindex",
    "ops migrate": "migrate",
    "ops rollback": "rollback",
    "ops pack list": "pack list",
    "ops pack show": "pack show",
    "ops pack validate": "pack validate",
    "ops pack create": "pack create",
    "ops pack use": "pack use",
    "ops sync": "sync",
    "ops sync install": "sync",
    "ops adapters render": "adapters sync",
    "ops measure versions": "measure versions",
    "ops measure fix": "measure fix",
    "serve mcp": "mcp serve",
    "serve ui": "ui serve",
}

#: Accepted spellings kept out of help for continuity with the flat surface.
COMPAT_ALIASES: dict[str, str] = {
    "inbox": "cto brief",
    "read brief": "cto brief",
    "repath": "relocate",
    "sync install": "sync",
    "adapters render": "adapters sync",
    "ops gate": "ci",
    "ops sync driver": "sync driver",
    "ops adapters sync": "adapters sync",
}

ALIASES: dict[str, str] = {**VISIBLE_ALIASES, **COMPAT_ALIASES}

#: The visible leaf paths, used to leave an already-grouped command untouched.
VISIBLE_PATHS: frozenset[str] = frozenset(VISIBLE_ALIASES) | {
    "init",
    "approve",
    "sync driver",
    "types",
    "next",
    "model",
    "explain",
    "why",
    "serve daemon",
}

#: Flat names accepted for continuity and where they move in the grouped surface.
LEGACY_TO_SURFACE: dict[str, str] = {
    "status": "read status",
    "digest": "read digest",
    "find": "read find",
    "show": "read show",
    "ledger": "read ledger",
    "cto brief": "overview",
    "cto tick": "loop tick",
    "add": "change add",
    "update": "change update",
    "move": "change move",
    "link": "change link",
    "rename": "change rename",
    "relocate": "change repath",
    "check": "check declare",
    "verify": "check run",
    "dispatch": "loop dispatch",
    "review": "loop review",
    "merge": "loop merge",
    "report": "loop report",
    "triage": "loop triage",
    "execution record": "loop execution",
    "ci": "gate",
    "doctor": "ops doctor",
    "reindex": "ops reindex",
    "migrate": "ops migrate",
    "rollback": "ops rollback",
    "pack list": "ops pack list",
    "pack show": "ops pack show",
    "pack validate": "ops pack validate",
    "pack create": "ops pack create",
    "pack use": "ops pack use",
    "measure versions": "ops measure versions",
    "measure fix": "ops measure fix",
    "sync": "ops sync",
    "sync install": "ops sync install",
    "sync driver": "sync driver",
    "adapters sync": "ops adapters render",
    "mcp serve": "serve mcp",
    "ui serve": "serve ui",
    "inbox": "overview",
    "read brief": "overview",
    "repath": "change repath",
    "adapters render": "ops adapters render",
    "ops gate": "gate",
    "ops sync driver": "sync driver",
    "ops adapters sync": "ops adapters render",
}

for _alias, _target in LEGACY_TO_SURFACE.items():
    if _target not in VISIBLE_PATHS:
        raise ValueError(f"legacy command {_alias!r} points outside the surface {_target!r}")
del _alias, _target

#: Flags an alias supplies when the caller does not name them.
ALIAS_DEFAULTS: dict[str, dict[str, object]] = {
    "sync install": {"install": True},
    "ops sync install": {"install": True},
}

#: One sentence for a group head that has no single canonical tool.
GROUP_SUMMARIES: dict[str, str] = {
    "read": "Inspect the picture, items, activity and runs.",
    "change": "Add, update, link, move, rename and repath documents.",
    "check": "Declare a Check or run one.",
    "loop": "Dispatch, review, merge, report and triage work.",
    "ops": "Maintain the store, packs, sync, adapters and measurements.",
    "pack": "Inspect, validate, create and switch packs.",
    "sync": "Integrate clone changes or install git drivers.",
    "adapters": "Render the managed agent files and skills.",
    "measure": "Compare versions and check whether a fix recurred.",
    "serve": "Open an MCP or HTTP transport.",
}

#: Aliases whose surface wording differs from their canonical tool.
ALIAS_SUMMARIES: dict[str, str] = {
    "overview": "The daily starting page: approval, executable work, waits and quality.",
    "gate": "Gate the store: doctor issues, approval and done gates; never runs Checks.",
    "change repath": "Move every document to the path its placement renders now.",
    "ops adapters render": "Render the managed AGENTS.md block and one skill file per skill Note.",
    "ops sync install": "Install the git merge drivers for store-only clones.",
}
for _alias in ALIAS_SUMMARIES:
    if _alias not in VISIBLE_ALIASES:
        raise ValueError(f"summary {_alias!r} does not name a visible alias")
del _alias

for _alias, _canonical in ALIASES.items():
    if _canonical in ALIASES:
        raise ValueError(f"alias {_alias!r} points at another alias {_canonical!r}")
    if not _canonical.strip() or _alias == _canonical:
        raise ValueError(f"alias {_alias!r} does not name a distinct canonical tool")
del _alias, _canonical


def canonical_tool(name: str) -> str:
    """The canonical tool a surface name resolves to."""
    return ALIASES.get(name, name)


def alias_defaults(name: str) -> dict[str, object]:
    """Arguments an alias supplies unless the caller overrides them."""
    return dict(ALIAS_DEFAULTS.get(name, {}))


def canonical_methods(names: Iterable[str]) -> frozenset[str]:
    """Canonicalise a profile's allowed methods for comparison."""
    return frozenset(canonical_tool(name) for name in names)
