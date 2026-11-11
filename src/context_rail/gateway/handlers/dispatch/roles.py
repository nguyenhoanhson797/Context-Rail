"""Role-driven dispatch: which role works one Item, and the run seat it acts as.

A role with a write workspace implements; one with a read workspace reviews. The
dispatcher picks by anchor and preset, may name one explicitly, and must itself
hold the method and the write reach. The worker's run seat is a session with no
TTL, live while its run is open.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from context_rail.context.seat_brief import seat_brief
from context_rail.core.util import now_iso
from context_rail.kernel.seats import LiveSeat
from context_rail.model.roles import check_profile_narrows
from context_rail.store.sessions import open_session
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.gateway.identity import Identity
    from context_rail.model.cto import Profile
    from context_rail.model.roles import Role

__all__ = ["code_reach_text", "open_run_seat", "pick_role", "run_seat_brief"]


def pick_role(
    ctx: Context,
    identity: Identity,
    *,
    item: str,
    preset: str,
    workspace: str,
    requested: str | None,
) -> Role:
    """The role that works ``item``, refused when none or several fit."""
    roles = ctx.pack.operating.roles
    if requested is not None:
        role = roles.get(requested)
        if role is None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"unknown role {requested!r}",
                {"reason": "role_unknown", "role": requested, "roles": sorted(roles)},
            )
        _check_role(ctx, identity, role, item, preset, workspace)
        return role
    candidates = [
        role
        for role in roles.values()
        if role.workspace == workspace
        and role.anchor.kind == "item"
        and preset in role.anchor.presets
    ]
    if not candidates:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"no {workspace} role anchors on {preset!r} items",
            {"reason": "role_not_found", "preset": preset, "workspace": workspace},
        )
    if len(candidates) > 1:
        raise WriteRefused(
            "CONFLICT",
            f"{len(candidates)} roles could work a {preset!r}; pass --role",
            {
                "reason": "ambiguous_role",
                "candidates": sorted(role.name for role in candidates),
            },
        )
    role = candidates[0]
    _check_role(ctx, identity, role, item, preset, workspace)
    return role


def _check_role(
    ctx: Context, identity: Identity, role: Role, item: str, preset: str, workspace: str
) -> None:
    if role.workspace != workspace:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"role {role.name!r} has workspace {role.workspace!r}, not {workspace!r}",
            {"reason": "role_workspace", "role": role.name, "workspace": role.workspace},
        )
    if role.anchor.kind != "item" or preset not in role.anchor.presets:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"role {role.name!r} does not anchor on {preset!r} items",
            {"reason": "role_anchor", "role": role.name, "preset": preset},
        )
    if identity.kind == "human":
        return
    if role.human:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"role {role.name!r} may only be dispatched by a verified human",
            {"reason": "human_role_requires_human", "role": role.name},
        )
    if identity.kind != "seat" or identity.role is None:
        raise WriteRefused(
            "PERMISSION_DENIED",
            "only the human or a live seat may dispatch",
            {"reason": "dispatch_denied"},
        )
    method = "dispatch" if workspace == "write" else "review"
    if not _allows(identity.role.can.methods.get(method), preset):
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"role {identity.role.name!r} may not {method} {preset!r}",
            {"reason": "method_not_in_role", "role": identity.role.name, "preset": preset},
        )
    reach = ctx.reach
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ? LIMIT 1", (item,)
    ).fetchone()
    if row is None or reach is None or not reach.can_write(item, path=row[0], preset=row[1]):
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"{item!r} is outside the dispatcher's write reach",
            {"reason": "out_of_reach", "item": item},
        )


def open_run_seat(
    ctx: Context,
    role: Role,
    *,
    item: str,
    dispatcher: Identity,
    profile_name: str,
    profile: Profile,
    dispatch_id: str,
) -> str:
    """Open the run-bound session for ``<role>@<dispatch_id>`` and return its token."""
    reason = check_profile_narrows(role, profile)
    if reason is not None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            reason,
            {"reason": "profile_widens_role", "profile": profile_name},
        )
    seat = f"{role.name}@{dispatch_id}"
    token, _session = open_session(
        ctx.store,
        seat,
        run=dispatch_id,
        role=role.name,
        anchor=item,
        profile=profile_name,
        granted_by=dispatcher.name,
        ttl_s=None,
    )
    return token


def run_seat_brief(
    context: Context,
    role: Role,
    *,
    seat: str,
    item: str,
    profile_name: str,
    dispatcher: str,
    extra_parts: Mapping[str, str] | None = None,
) -> str:
    """The brief a run seat may read, rendered as the order's context section."""
    live = LiveSeat(
        seat=seat,
        role=role.name,
        anchor=item,
        profile=profile_name,
        granted_by=dispatcher,
        granted_at=now_iso(),
    )
    return seat_brief(context, live, role, extra_parts=extra_parts)["text"]


def code_reach_text(role: Role) -> str:
    """The role's code write patterns as the work order states them."""
    return ", ".join(pattern.raw for pattern in role.code_write) or "(none)"


def _allows(value: Any, preset: str) -> bool:
    if value is True:
        return True
    if isinstance(value, (list, tuple)):
        return preset in value
    return False
