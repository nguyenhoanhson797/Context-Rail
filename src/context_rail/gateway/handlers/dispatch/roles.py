"""Role-driven dispatch: which role works one Item, and the run seat it acts as.

A role with a write workspace implements; one with a read workspace reviews. The
dispatcher picks by anchor and preset, may name one explicitly, and must itself
hold the method and the write reach. The worker's run seat is a session with no
TTL, live while its run is open.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from context_rail.context.reach import ReachError, resolve_reach
from context_rail.context.seat_brief import seat_brief
from context_rail.core.util import now_iso
from context_rail.kernel.seats import LiveSeat
from context_rail.model.roles import check_profile_narrows
from context_rail.store import query
from context_rail.store.sessions import open_session
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.gateway.identity import Identity
    from context_rail.model.cto import Profile
    from context_rail.model.roles import Role

__all__ = [
    "code_reach_text",
    "code_reach_violations",
    "independence_refusal",
    "open_run_seat",
    "pick_role",
    "refuse_code_reach",
    "run_seat_brief",
]


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


def independence_refusal(
    ctx: Context,
    *,
    item: str,
    step_kinds: Sequence[str],
    acting: Sequence[str],
) -> str | None:
    """Why the acting seats are not independent of the item's listed steps.

    ``acting`` names the acting seat and its profile. The producing and
    reviewing run seats are compared by seat and profile; a ``grant`` step
    compares the seat that granted the run seat. The acting dispatcher is not
    part of ``acting``: one lead may dispatch both sides.
    """
    names = {name for name in acting if isinstance(name, str) and name}
    if not names:
        return None
    for kind in step_kinds:
        if kind == "produce":
            prior = _run_names(ctx, item, "implementer", ("seat", "profile"))
        elif kind == "review":
            prior = _run_names(ctx, item, "reviewer", ("seat", "profile"))
        elif kind == "grant":
            prior = _run_names(ctx, item, None, ("dispatcher",))
        else:
            continue
        overlap = sorted(names & prior)
        if overlap:
            return (
                f"the {kind} step on {item!r} was already performed by "
                + ", ".join(overlap)
            )
    return None


def _run_names(
    ctx: Context, item: str, role: str | None, fields: Sequence[str]
) -> set[str]:
    clause = "item = ?"
    params: list[Any] = [item]
    if role is not None:
        clause += " AND role = ?"
        params.append(role)
    rows = ctx.index.conn.execute(
        f"SELECT record_id FROM runs WHERE {clause}", params
    ).fetchall()
    found: set[str] = set()
    for (run_id,) in rows:
        record = query.run_record(ctx.index, run_id)
        if record is None:
            continue
        data = record.get("data") or {}
        for field in fields:
            value = data.get(field)
            if field == "profile" and not value:
                value = record.get("profile")
            if isinstance(value, str) and value:
                found.add(value)
    return found


def code_reach_violations(
    ctx: Context, run_data: Mapping[str, Any]
) -> list[str]:
    """The run's changed files outside the producing seat's code write reach.

    A legacy run carries no ``seat_role`` and skips the check. A run whose
    role or anchor cannot be resolved fails closed: every changed file is a
    violation, never none.
    """
    files = [
        path.replace("\\", "/")
        for path in run_data.get("files_changed") or ()
        if isinstance(path, str)
    ]
    seat_role = run_data.get("seat_role")
    if not isinstance(seat_role, str) or not seat_role:
        return []
    role = ctx.pack.operating.roles.get(seat_role)
    anchor = run_data.get("item")
    if role is None or not isinstance(anchor, str) or not anchor:
        return files
    live = LiveSeat(
        seat=str(run_data.get("seat") or f"{seat_role}@run"),
        role=seat_role,
        anchor=anchor,
        profile=run_data.get("profile") or run_data.get("actor"),
        granted_by=str(run_data.get("dispatcher") or "human"),
        granted_at=str(run_data.get("started_at") or ""),
    )
    try:
        reach = resolve_reach(ctx.index, ctx.pack, role, live)
    except ReachError:
        return files
    return [path for path in files if not reach.can_write_code(path)]


def refuse_code_reach(ctx: Context, run_data: Mapping[str, Any]) -> None:
    """Refuse a run whose changed files leave its producing seat's code reach."""
    violations = code_reach_violations(ctx, run_data)
    if not violations:
        return
    role = ctx.pack.operating.roles.get(str(run_data.get("seat_role") or ""))
    reach = [pattern.raw for pattern in role.code_write] if role is not None else []
    raise WriteRefused(
        "BUSINESS_RULE_VIOLATED",
        "the run changed files outside its code write reach: "
        + ", ".join(violations),
        {"paths": violations, "reach": reach},
    )


def _allows(value: Any, preset: str) -> bool:
    if value is True:
        return True
    if isinstance(value, (list, tuple)):
        return preset in value
    return False
