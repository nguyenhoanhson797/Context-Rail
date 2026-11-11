"""Role-mode authorization: a seat acts through its role and its reach.

A store whose pack declares roles decides every write from the seat's role and
what the seat can reach, never from a name. Reads stay open here; Task 24 scopes
them. The guard fails closed: a write tool with no rule is refused rather than
allowed, so a new command cannot quietly become a seat capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from context_rail.core.result import fail
from context_rail.model.roles import role_tools
from context_rail.store.index import Index

if TYPE_CHECKING:
    from context_rail.context.reach import ReachSet
    from context_rail.gateway.gateway import Context
    from context_rail.gateway.tools import Tool

__all__ = [
    "OPS_HUMAN_ONLY",
    "Targets",
    "authorize_role",
    "targets_for",
]

OPS_HUMAN_ONLY = frozenset(
    {
        "init",
        "migrate",
        "rollback",
        "adapters sync",
        "sync",
        "sync driver",
        "measure fix",
        "codegraph setup",
        "codegraph refresh",
        "codegraph adopt",
        "pack create",
        "pack use",
    }
)

SESSION_COMMAND = "rail seat open <seat> -- <command>"
_SEAT_COMMANDS = frozenset(
    {"seat list", "seat open", "seat close", "seat grant", "seat revoke"}
)
_WRITE_TABLE = frozenset({"add", "update", "rename", "relocate", "move"})


@dataclass(frozen=True)
class Targets:
    """The documents one call would touch, derived from its parameters."""

    writes: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    preset: str | None = None
    parent: str | None = None
    status: str | None = None
    relation: str | None = None


def targets_for(
    tool_name: str, params: Mapping[str, Any], index: Index
) -> Targets:
    """The targets this tool's parameters name, resolved where the index can."""
    if tool_name == "add":
        preset = _text(params.get("preset"))
        parent = _relation_target(params.get("rel"), "part_of")
        return Targets(
            writes=(parent,) if parent else (),
            preset=preset,
            parent=parent,
            relation="part_of" if parent else None,
        )
    if tool_name in ("update", "move"):
        slug = _text(params.get("slug"))
        return Targets(
            writes=(slug,) if slug else (),
            preset=_preset(index, slug),
            status=_text(params.get("status")) if tool_name == "move" else None,
        )
    if tool_name == "rename":
        slug = _text(params.get("old"))
        return Targets(
            writes=(slug,) if slug else (),
            preset=_preset(index, slug),
        )
    return Targets()


def authorize_role(
    tool: Tool, params: dict[str, Any], context: Context
) -> dict[str, Any] | None:
    """The refusal a seat call earns, or ``None`` when its role and reach allow it."""
    identity = context.identity
    if identity.kind == "anonymous":
        if tool.permission == "read":
            return None
        return fail(
            "SESSION_REQUIRED",
            "a tokenless caller may only read; open a seat session to write",
            {"reason": "session_required", "command": SESSION_COMMAND},
        )
    if identity.view_as is not None and tool.permission != "read":
        return fail(
            "PERMISSION_DENIED",
            "a seat lens is read-only",
            {"reason": "lens_read_only", "seat": identity.view_as.seat},
        )
    if identity.kind == "human":
        return None
    if identity.kind != "seat" or identity.role is None or identity.seat is None:
        return fail(
            "PERMISSION_DENIED",
            "this call needs a live seat",
            {"reason": "method_not_in_role"},
        )
    if tool.name in _SEAT_COMMANDS:
        return None
    if tool.name in OPS_HUMAN_ONLY:
        return fail(
            "PERMISSION_DENIED",
            f"{tool.name!r} is a human operation, not a seat's",
            {"reason": "ops_human_only", "tool": tool.name},
        )
    if tool.permission == "read":
        return None
    if tool.name not in _WRITE_TABLE:
        return _refuse(
            "tool_not_for_seats",
            f"{tool.name!r} has no seat rule; it is refused rather than guessed",
            _hint(context.reach, identity.name),
        )
    role = identity.role
    allowed = role_tools(role)
    if tool.name not in allowed:
        return _refuse(
            "method_not_in_role",
            f"role {role.name!r} may not call {tool.name!r}",
            _hint(context.reach, identity.name),
            tools=sorted(allowed),
        )
    reach = context.reach
    if reach is None:
        return _refuse("method_not_in_role", "the seat has no reach", None)
    targets = targets_for(tool.name, params, context.index)
    if tool.name == "relocate":
        if not reach.write_all:
            return _refuse(
                "tool_not_for_seats",
                "relocating the store needs project write reach",
                _hint(reach, identity.name),
            )
        return None
    if tool.name == "add":
        return _check_add(context, role, reach, targets)
    return _check_document(context, tool.name, role, reach, targets, identity.name)


def _check_add(
    context: Context, role: Any, reach: ReachSet, targets: Targets
) -> dict[str, Any] | None:
    preset = targets.preset
    if not preset or not _allows(role.can.methods.get("add"), preset):
        return _refuse(
            "preset_not_in_can_add",
            f"role {role.name!r} may not add {preset!r}",
            _hint(reach, context.identity.name),
        )
    if targets.parent is not None:
        if not _writes(context, reach, targets.parent):
            return _refuse(
                "add_outside_reach",
                f"the parent {targets.parent!r} is outside the seat's write reach",
                _hint(reach, context.identity.name),
                parent=targets.parent,
            )
        return None
    if reach.write_all or preset in reach.write_presets:
        return None
    return _refuse(
        "add_without_parent",
        "adding outside a parent needs project write reach or a declared write preset",
        _hint(reach, context.identity.name),
        preset=preset,
    )


def _check_document(
    context: Context,
    tool_name: str,
    role: Any,
    reach: ReachSet,
    targets: Targets,
    seat: str,
) -> dict[str, Any] | None:
    slug = targets.writes[0] if targets.writes else None
    if slug is None or not _writes(context, reach, slug):
        return _refuse(
            "write_out_of_reach",
            f"{slug or 'the document'!r} is outside the seat's write reach",
            _hint(reach, seat),
        )
    if tool_name == "move":
        status = targets.status
        moves = role.can.methods.get("move")
        permitted = moves.get(targets.preset) if isinstance(moves, dict) else None
        if status is None or not _allows(permitted, status):
            return _refuse(
                "status_not_in_can_move",
                f"role {role.name!r} may not move {targets.preset!r} to {status!r}",
                _hint(reach, seat),
            )
        return None
    if not _allows(role.can.methods.get(tool_name), targets.preset):
        return _refuse(
            f"preset_not_in_can_{tool_name}",
            f"role {role.name!r} may not {tool_name} {targets.preset!r}",
            _hint(reach, seat),
        )
    return None


def _writes(context: Context, reach: ReachSet, slug: str) -> bool:
    row = _doc(context.index, slug)
    if row is None:
        return False
    return reach.can_write(slug, path=row[0], preset=row[1])


def _doc(index: Index, slug: str | None) -> tuple[str, str] | None:
    if not slug:
        return None
    row = index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ?", (slug,)
    ).fetchone()
    return None if row is None else (row[0], row[1])


def _preset(index: Index, slug: str | None) -> str | None:
    row = _doc(index, slug)
    return None if row is None else row[1]


def _relation_target(entries: Any, relation: str) -> str | None:
    if isinstance(entries, str):
        entries = (entries,)
    if not isinstance(entries, (list, tuple)):
        return None
    for entry in entries:
        if isinstance(entry, str):
            name, _, slug = entry.partition("=")
            if name == relation and slug:
                return slug
    return None


def _allows(value: Any, name: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (list, tuple)):
        return name in value
    return False


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _hint(reach: ReachSet, seat: str) -> str:
    if reach.write_all:
        return f"seat {seat!r} may write the whole project"
    written = ", ".join(sorted(reach.write_slugs)[:8]) or "nothing"
    return f"seat {seat!r} may write: {written}"


def _refuse(
    reason: str,
    message: str,
    reach_hint: str | None,
    **details: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reason": reason, **details}
    if reach_hint:
        payload["reach_hint"] = reach_hint
    return fail("PERMISSION_DENIED", message, payload)
