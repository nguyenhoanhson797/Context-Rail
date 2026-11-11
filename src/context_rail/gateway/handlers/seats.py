"""Seat commands: which seats hold which roles, and granting a new one."""

from __future__ import annotations

from typing import Any

from context_rail.context.reach import ReachError, resolve_reach
from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.common import profile_document
from context_rail.gateway.tools import reserved_seat_names
from context_rail.kernel.seats import fold
from context_rail.model.roles import check_profile_narrows, is_name, Role
from context_rail.model.seats import HUMAN, SEAT_GRANTED, SEAT_KINDS, SEAT_REVOKED
from context_rail.store import query
from context_rail.store.lock import store_lock
from context_rail.store.write import WriteRefused, commit

_PROJECT = "project"
_SKILL_PRESET = "skill"

__all__ = ["seat_grant", "seat_list", "seat_revoke"]


def seat_list(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The live seats; ``all`` lists every grant and marks which one is live."""
    if not ctx.role_mode:
        return {"seats": [], "role_mode": False}
    events = query.seat_events(ctx.index)
    live = fold(events)
    rows: list[dict[str, Any]] = []
    if args.get("all"):
        for event in events:
            if event.kind != SEAT_GRANTED:
                continue
            current = live.get(event.seat)
            is_live = current is not None and (
                current.role,
                current.anchor,
                current.profile,
                current.granted_by,
                current.granted_at,
            ) == (event.role, event.anchor, event.profile, event.by, event.at)
            rows.append(
                _row(
                    event.seat,
                    event.role,
                    event.anchor,
                    event.profile,
                    event.by,
                    is_live,
                )
            )
    else:
        for seat in sorted(
            live.values(), key=lambda item: (item.granted_at, item.seat)
        ):
            rows.append(
                _row(
                    seat.seat,
                    seat.role,
                    seat.anchor,
                    seat.profile,
                    seat.granted_by,
                    True,
                )
            )
    return {"seats": rows, "role_mode": True}


def seat_grant(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Grant a seat a role on an anchor the granter can write.

    Re-checks the live names and the granter's liveness inside the store lock so
    two grants of one name cannot both win.
    """
    if not ctx.role_mode:
        raise WriteRefused(
            "INVALID_STATE",
            "this pack declares no roles; grant seats only in a store with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    identity = ctx.identity
    seat = _text(args.get("seat"), "seat")
    role_name = _text(args.get("role"), "role")
    anchor = _text(args.get("anchor") or _PROJECT, "anchor")
    profile_name = _named(args.get("profile"))
    role = ctx.pack.operating.roles.get(role_name)
    if role is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"unknown role {role_name!r}",
            {
                "reason": "role_unknown",
                "role": role_name,
                "roles": sorted(ctx.pack.operating.roles),
            },
        )
    _check_granter(identity, role_name)
    _check_name(seat)
    _check_anchor(ctx, role, anchor)
    if identity.kind != "human" and role.human:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"role {role_name!r} may only be held by a verified human",
            {"reason": "human_role_requires_human", "role": role_name},
        )
    if not _inside_write_reach(ctx, anchor):
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"{anchor!r} is outside the granter's write reach",
            {"reason": "anchor_out_of_reach", "anchor": anchor},
        )
    if profile_name is not None:
        _rel, _etag, profile = profile_document(ctx, profile_name)
        reason = check_profile_narrows(role, profile)
        if reason is not None:
            code = (
                "profile_auto_high"
                if profile.auto == "high" and "tick" not in role.can.methods
                else "profile_widens_role"
            )
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"reason": code, "profile": profile_name},
            )
    missing = _missing_skills(ctx, role)
    if missing:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "role skill note(s) missing: " + ", ".join(missing),
            {"reason": "role_skill_missing", "skills": missing},
        )
    record = {
        "kind": SEAT_GRANTED,
        "about": [] if anchor == _PROJECT else [anchor],
        "data": {
            "schema": 1,
            "seat": seat,
            "role": role_name,
            "anchor": anchor,
            "profile": profile_name,
            "granted_by": identity.name,
        },
    }
    with store_lock(ctx.store):
        ctx.index.sync()
        live = query.live_seats(ctx.index)
        if seat in live:
            raise WriteRefused(
                "CONFLICT",
                f"seat {seat!r} is already live",
                {"reason": "seat_name_taken", "seat": seat},
            )
        if identity.kind == "seat" and identity.name not in live:
            raise WriteRefused(
                "PERMISSION_DENIED",
                f"seat {identity.name!r} is no longer live",
                {"reason": "granter_not_live", "seat": identity.name},
            )
        commit(
            ctx.store,
            ctx.manifest,
            [],
            record,
            actor=identity.name,
            key=args.get("key"),
            index=ctx.index,
            reserved_kinds=frozenset(SEAT_KINDS),
        )
    return {
        "seat": seat,
        "role": role_name,
        "anchor": anchor,
        "profile": profile_name,
        "granted_by": identity.name,
        "live": True,
    }


def seat_revoke(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Revoke a live seat; every seat it granted stops living in the fold.

    Only the human or an ancestor in the target's grant lineage may revoke, and
    no seat may revoke itself. The check runs again inside the store lock.
    """
    if not ctx.role_mode:
        raise WriteRefused(
            "INVALID_STATE",
            "this pack declares no roles; revoke seats only in a store with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    identity = ctx.identity
    seat = _text(args.get("seat"), "seat")
    with store_lock(ctx.store):
        ctx.index.sync()
        live = query.live_seats(ctx.index)
        target = live.get(seat)
        if target is None:
            raise WriteRefused(
                "NOT_FOUND",
                f"no live seat {seat!r}",
                {"reason": "seat_unknown", "seat": seat, "live": sorted(live)},
            )
        _check_revoker(identity, seat, live)
        record = {
            "kind": SEAT_REVOKED,
            "about": [] if target.anchor == _PROJECT else [target.anchor],
            "data": {
                "schema": 1,
                "seat": seat,
                "revoked_by": identity.name,
            },
        }
        commit(
            ctx.store,
            ctx.manifest,
            [],
            record,
            actor=identity.name,
            key=args.get("key"),
            index=ctx.index,
            reserved_kinds=frozenset(SEAT_KINDS),
        )
    return {"seat": seat, "revoked_by": identity.name, "live": False}


def _check_revoker(
    identity: Any, seat: str, live: dict[str, Any]
) -> None:
    if identity.kind == "human":
        return
    if identity.kind != "seat" or identity.name == seat:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"seat {identity.name!r} may not revoke {seat!r}",
            {"reason": "revoke_denied", "seat": seat},
        )
    seen: set[str] = set()
    parent = live[seat].granted_by
    while parent != HUMAN and parent not in seen:
        if parent == identity.name:
            return
        seen.add(parent)
        holder = live.get(parent)
        if holder is None:
            break
        parent = holder.granted_by
    raise WriteRefused(
        "PERMISSION_DENIED",
        f"seat {identity.name!r} may not revoke {seat!r}",
        {"reason": "revoke_denied", "seat": seat},
    )


def _check_granter(identity: Any, role_name: str) -> None:
    if identity.kind == "human":
        return
    if identity.kind != "seat" or identity.role is None:
        raise WriteRefused(
            "PERMISSION_DENIED",
            "only the human or a live seat may grant a seat",
            {"reason": "grant_denied"},
        )
    permitted = identity.role.can.methods.get("grant") or ()
    if role_name not in permitted:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"seat {identity.name!r} may not grant role {role_name!r}",
            {"reason": "grant_denied", "role": role_name, "can_grant": list(permitted)},
        )


def _check_name(seat: str) -> None:
    if not is_name(seat):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{seat!r} is not a valid seat name; use a lowercase slug",
            {"reason": "seat_name_invalid", "seat": seat},
        )
    if seat in reserved_seat_names():
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{seat!r} collides with a command or reserved name",
            {"reason": "seat_name_reserved", "seat": seat},
        )


def _check_anchor(ctx: Context, role: Role, anchor: str) -> None:
    if role.anchor.kind == _PROJECT:
        matches = anchor == _PROJECT
    else:
        row = _anchor_doc(ctx, anchor)
        matches = row is not None and row[1] in role.anchor.presets
    if not matches:
        wanted = (
            _PROJECT if role.anchor.kind == _PROJECT else ", ".join(role.anchor.presets)
        )
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"anchor {anchor!r} does not match role anchor kind {role.anchor.kind!r} "
            f"({wanted})",
            {"reason": "anchor_kind_mismatch", "anchor": anchor, "role": role.name},
        )


def _inside_write_reach(ctx: Context, anchor: str) -> bool:
    identity = ctx.identity
    if identity.kind == "human":
        return True
    if identity.role is None or identity.seat is None:
        return False
    try:
        reach = resolve_reach(ctx.index, ctx.pack, identity.role, identity.seat)
    except ReachError as exc:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"the granter's reach cannot be resolved: {exc}",
            {"reason": "anchor_out_of_reach", "anchor": anchor},
        ) from exc
    if anchor == _PROJECT:
        return reach.write_all
    row = _anchor_doc(ctx, anchor)
    if row is None:
        return False
    return reach.can_write(anchor, path=row[0], preset=row[1])


def _missing_skills(ctx: Context, role: Role) -> list[str]:
    missing: list[str] = []
    for skill in role.skills:
        row = ctx.index.conn.execute(
            "SELECT preset FROM docs WHERE slug = ?", (skill,)
        ).fetchone()
        if row is None or row[0] != _SKILL_PRESET:
            missing.append(skill)
    return missing


def _anchor_doc(ctx: Context, anchor: str) -> tuple[str, str] | None:
    row = ctx.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ?", (anchor,)
    ).fetchone()
    return None if row is None else (row[0], row[1])


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"{key} must be non-empty text", {"reason": f"{key}_invalid"}
        )
    return value.strip()


def _named(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _row(
    seat: str,
    role: str | None,
    anchor: str | None,
    profile: str | None,
    granted_by: str,
    live: bool,
) -> dict[str, Any]:
    return {
        "seat": seat,
        "role": role,
        "anchor": anchor,
        "profile": profile,
        "granted_by": granted_by,
        "live": live,
    }
