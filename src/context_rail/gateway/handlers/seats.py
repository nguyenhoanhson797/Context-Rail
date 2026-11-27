"""Seat commands: which seats hold which roles, and granting a new one."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from context_rail.context import recipes
from context_rail.context.reach import ReachError, resolve_reach
from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.common import (
    profile_document,
    read_refusal,
    skill_pins_for,
)
from context_rail.gateway.tools import reserved_seat_names
from context_rail.kernel.seats import fold
from context_rail.model.roles import check_profile_narrows, is_name, Role
from context_rail.model.seats import HUMAN, SEAT_GRANTED, SEAT_KINDS, SEAT_REVOKED
from context_rail.store import query
from context_rail.store.location import local_dir
from context_rail.store.lock import store_lock
from context_rail.store.sessions import (
    SESSIONS_DIR,
    close_all_sessions,
    close_sessions,
    drop_expired_sessions,
    open_session,
)
from context_rail.store.write import WriteRefused, commit

_PROJECT = "project"
_SKILL_PRESET = "skill"
_MAX_TTL_HOURS = 168
_DEFAULT_TTL_HOURS = 12
_QUEUE_HEAD = 5

__all__ = [
    "seat_close",
    "seat_grant",
    "seat_list",
    "seat_open",
    "seat_revoke",
    "seat_show",
]


def seat_list(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The live seats; ``all`` lists every grant and marks which one is live."""
    if not ctx.role_mode:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "this pack declares no roles; seat commands need a pack with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
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
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"reason": "profile_widens_role", "profile": profile_name},
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
    closed = close_sessions(ctx.store, seat=seat)
    return {"seat": seat, "revoked_by": identity.name, "live": False, "closed": closed}


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
    if _is_ancestor(identity.name, seat, live):
        return
    raise WriteRefused(
        "PERMISSION_DENIED",
        f"seat {identity.name!r} may not revoke {seat!r}",
        {"reason": "revoke_denied", "seat": seat},
    )


def seat_open(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Open a session for a live seat the caller may act through.

    The token is present only with ``print_env``; a tool puts it in
    ``RAIL_SESSION`` for the process that should act as the seat.
    """
    if not ctx.role_mode:
        raise WriteRefused(
            "INVALID_STATE",
            "this pack declares no roles; open sessions only in a store with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    identity = ctx.identity
    drop_expired_sessions(ctx.store)
    seat = _text(args.get("seat"), "seat")
    hours = _ttl_hours(args.get("ttl_hours"))
    live = query.live_seats(ctx.index)
    target = live.get(seat)
    if target is None:
        raise WriteRefused(
            "NOT_FOUND",
            f"no live seat {seat!r}",
            {"reason": "seat_unknown", "seat": seat, "live": sorted(live)},
        )
    _check_opener(ctx, identity, seat, target, live)
    token, session = open_session(
        ctx.store,
        seat,
        role=target.role,
        anchor=target.anchor,
        profile=target.profile,
        granted_by=target.granted_by,
        ttl_s=hours * 3600,
    )
    expires = (
        None
        if session.expires is None
        else datetime.fromtimestamp(session.expires, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    value: dict[str, Any] = {"seat": seat, "expires": expires}
    if args.get("print_env"):
        value["token"] = token
        value["note"] = (
            "put this token in RAIL_SESSION for the process that should act as "
            "the seat; never paste it into a conversation"
        )
    return value


def seat_close(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Close a seat's sessions, or every known seat's sessions with ``all``."""
    if not ctx.role_mode:
        raise WriteRefused(
            "INVALID_STATE",
            "this pack declares no roles; close sessions only in a store with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    identity = ctx.identity
    if args.get("all") is True:
        if identity.kind != "human":
            raise WriteRefused(
                "PERMISSION_DENIED",
                "only the human may close every session",
                {"reason": "close_denied"},
            )
        return {"all": True, "closed": close_all_sessions(ctx.store)}
    seat = _named(args.get("seat"))
    if seat is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "seat close needs a seat or --all",
            {"reason": "seat_required"},
        )
    live = query.live_seats(ctx.index)
    if seat not in live:
        raise WriteRefused(
            "NOT_FOUND",
            f"no live seat {seat!r}",
            {"reason": "seat_unknown", "seat": seat, "live": sorted(live)},
        )
    if identity.kind != "human" and identity.name != seat:
        if identity.kind != "seat" or not _is_ancestor(identity.name, seat, live):
            raise WriteRefused(
                "PERMISSION_DENIED",
                f"seat {identity.name!r} may not close {seat!r}",
                {"reason": "close_denied", "seat": seat},
            )
    return {"seat": seat, "closed": close_sessions(ctx.store, seat=seat)}


def _check_opener(
    ctx: Context, identity: Any, seat: str, target: Any, live: dict[str, Any]
) -> None:
    if identity.kind == "human":
        return
    if identity.kind != "seat":
        raise WriteRefused(
            "PERMISSION_DENIED",
            "only the human or a live seat may open a session; the project "
            "owner opens it in a terminal or in the local UI with "
            "`rail seat open <seat> --print-env`, or binds the store once with "
            "`rail agent enable <seat> --role <role> --anchor <anchor>`",
            {"reason": "open_denied"},
        )
    role = ctx.pack.operating.roles.get(target.role)
    if role is not None and role.human:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"seat {seat!r} holds a human-only role",
            {"reason": "human_role_requires_human", "seat": seat},
        )
    if identity.name == seat or not _is_ancestor(identity.name, seat, live):
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"seat {identity.name!r} may not open {seat!r}",
            {"reason": "open_denied", "seat": seat},
        )


def _is_ancestor(granter: str, seat: str, live: dict[str, Any]) -> bool:
    """Whether ``granter`` appears above ``seat`` in the live grant lineage."""
    seen: set[str] = set()
    parent = live[seat].granted_by
    while parent != HUMAN and parent not in seen:
        if parent == granter:
            return True
        seen.add(parent)
        holder = live.get(parent)
        if holder is None:
            return False
        parent = holder.granted_by
    return False


def _ttl_hours(value: Any) -> int:
    hours = _DEFAULT_TTL_HOURS if value is None else value
    if not isinstance(hours, int) or isinstance(hours, bool) or hours < 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "ttl_hours must be a positive whole number",
            {"reason": "ttl_invalid"},
        )
    if hours > _MAX_TTL_HOURS:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"ttl_hours must be at most {_MAX_TTL_HOURS}",
            {"reason": "ttl_too_long", "max": _MAX_TTL_HOURS},
        )
    return hours


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


def seat_show(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """One seat's role, reach, skills, budget, workspace, session and queue head.

    A seat sees itself; a seat whose read reach covers another seat's anchor
    sees that card; the human (terminal or channel) may view any live seat.
    The card never writes.
    """
    requested = _named(args.get("seat"))
    if not ctx.role_mode:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"unknown command {requested or 'seat'!r}; this pack declares no roles, "
            "so seat lenses are unavailable",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    identity = ctx.identity
    live = query.live_seats(ctx.index)
    if requested is None:
        if identity.seat is None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "seat show needs a seat: this caller has none",
                {"reason": "seat_required"},
            )
        target = identity.seat
    else:
        target = live.get(requested)
        if target is None:
            raise WriteRefused(
                "NOT_FOUND",
                f"no live seat is named {requested!r}",
                {
                    "reason": "seat_unknown",
                    "seat": requested,
                    "candidates": sorted(live),
                },
            )
        _check_card_view(ctx, identity, target)
    role = ctx.pack.operating.roles.get(target.role)
    if role is None:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"seat {target.seat!r} holds role {target.role!r}, which the pack no longer declares",
            {"reason": "seat_role_unknown", "seat": target.seat, "role": target.role},
        )
    try:
        reach = resolve_reach(ctx.index, ctx.pack, role, target)
    except ReachError as exc:
        raise WriteRefused(
            "PERMISSION_DENIED",
            str(exc),
            {"reason": "reach_unresolved", "seat": target.seat},
        ) from exc
    pins: list[dict[str, Any]] = []
    problems: list[str] = []
    for skill in role.skills:
        try:
            pins.extend(skill_pins_for(ctx, [skill]))
        except WriteRefused as exc:
            problems.append(exc.message)
    session = (
        _session_expiry(ctx.store, identity.session)
        if identity.session is not None and identity.name == target.seat
        else None
    )
    caller_reach = ctx.reach
    caller_readable = (
        None
        if caller_reach is None
        else {
            row.slug
            for row in recipes.item_rows(ctx.index, ctx.pack, reach=caller_reach)
        }
    )
    head = [
        row.slug
        for row in recipes.item_rows(ctx.index, ctx.pack, reach=reach)
        if row.category in ("not_started", "active")
        and not _is_container(ctx, row.preset)
        and (caller_readable is None or row.slug in caller_readable)
    ][:_QUEUE_HEAD]
    return {
        "seat": target.seat,
        "role": target.role,
        "anchor": _anchor_card(ctx, target.anchor),
        "reach": {
            "documents": len(reach.read_slugs),
            "read_presets": sorted(reach.read_presets),
            "read_prefixes": list(reach.read_prefixes),
            "write_prefixes": list(reach.write_prefixes),
            "code_read": list(reach.code_read),
            "code_write": list(reach.code_write),
        },
        "can": dict(role.can.methods),
        "skills": pins,
        "budget": {"chars": role.sees.chars, "docs": role.sees.docs},
        "workspace": role.workspace,
        "session": session,
        **({"queue_head": head} if _card_anchor_readable(ctx, target) else {}),
        "problems": problems,
    }


def _card_anchor_readable(ctx: Context, target: Any) -> bool:
    """Whether the caller holds read reach on the seat card's anchor."""
    if target.anchor == _PROJECT:
        return ctx.reach is None or ctx.reach.read_all
    if ctx.reach is None:
        return True
    row = ctx.index.conn.execute(
        "SELECT path FROM docs WHERE slug = ? LIMIT 1", (target.anchor,)
    ).fetchone()
    if row is None:
        return False
    return read_refusal(ctx, target.anchor, path=row[0]) is None


def _check_card_view(ctx: Context, identity: Any, target: Any) -> None:
    """Refuse a card whose anchor the caller may not read, except for its own seat."""
    if identity.kind == "human":
        return
    if identity.name == target.seat:
        return
    if _card_anchor_readable(ctx, target):
        return
    raise WriteRefused(
        "PERMISSION_DENIED",
        f"seat {target.seat!r} is outside the caller's read reach",
        {"reason": "out_of_reach", "seat": target.seat, "anchor": target.anchor},
    )


def _is_container(ctx: Context, preset: str | None) -> bool:
    declared = ctx.pack.presets.get(preset or "")
    return declared is not None and declared.container


def _anchor_card(ctx: Context, anchor: str) -> dict[str, Any]:
    if anchor == _PROJECT:
        return {"slug": _PROJECT, "title": _PROJECT, "status": None}
    row = ctx.index.conn.execute(
        "SELECT title, status FROM docs WHERE slug = ? LIMIT 1", (anchor,)
    ).fetchone()
    return {
        "slug": anchor,
        "title": (row[0] if row is not None and row[0] else anchor),
        "status": None if row is None else row[1],
    }


def _session_expiry(store: Any, session_id: str | None) -> dict[str, Any] | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    path = local_dir(store) / SESSIONS_DIR / f"{session_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    expires = payload.get("expires") if isinstance(payload, dict) else None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    return {
        "expires": datetime.fromtimestamp(expires, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    }


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
