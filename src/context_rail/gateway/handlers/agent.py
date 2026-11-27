"""The local agent credential: one seat a non-terminal caller uses automatically.

A store that declares roles normally needs a session token. A single-agent local
project should not have to carry that token in its environment or paste it into a
conversation. ``agent enable`` is the one human step: it grants the seat if
needed, opens a session and stores it in the store's local directory. The CLI
uses that credential for non-terminal callers when no ``RAIL_SESSION`` is set.
"""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context_rail.core.locate import AGENT_TOKEN
from context_rail.core.util import write_bytes_atomic
from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.seats import seat_grant
from context_rail.store import query
from context_rail.store.location import local_dir
from context_rail.store.sessions import close_sessions, open_session, resolve_session
from context_rail.store.write import WriteRefused

__all__ = ["AGENT_TOKEN", "agent_token_path", "disable", "enable", "show"]

_PROJECT = "project"


def agent_token_path(store: Path) -> Path:
    """The local credential file, outside the store and its git tree."""
    return local_dir(store) / AGENT_TOKEN


def enable(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Bind one live seat to this store and write its local credential."""
    if not ctx.role_mode:
        raise WriteRefused(
            "INVALID_STATE",
            "this pack declares no roles; a local agent credential needs a pack with roles",
            {"reason": "no_roles", "command": "rail pack use <pack>"},
        )
    _human(ctx)
    live = query.live_seats(ctx.index)
    seat = _seat_name(ctx, live, args.get("seat"))
    target = live.get(seat)
    if target is not None:
        role_name = target.role if args.get("role") is None else _text(args.get("role"), "role")
        anchor = target.anchor if args.get("anchor") is None else _text(args.get("anchor"), "anchor")
    else:
        role_name = _role_name(ctx, args.get("role"))
        anchor = _text(args.get("anchor") or _PROJECT, "anchor")
    profile_name = args.get("profile")
    if profile_name is not None:
        profile_name = _text(profile_name, "profile")

    if target is None:
        seat_grant(
            ctx,
            {
                "seat": seat,
                "role": role_name,
                "anchor": anchor,
                "profile": profile_name,
            },
            actor,
        )
        target = query.live_seats(ctx.index).get(seat)
    if target is None:
        raise WriteRefused(
            "INVALID_STATE",
            f"seat {seat!r} was not granted",
            {"reason": "seat_not_live", "seat": seat},
        )
    if target.role != role_name or target.anchor != anchor:
        raise WriteRefused(
            "CONFLICT",
            f"seat {seat!r} is live as {target.role!r} on {target.anchor!r}; "
            f"revoke it before binding it as {role_name!r} on {anchor!r}",
            {
                "reason": "seat_binding_mismatch",
                "seat": seat,
                "role": target.role,
                "anchor": target.anchor,
            },
        )

    _reset_old_credential(ctx)
    close_sessions(ctx.store, seat=seat)
    token, _session = open_session(
        ctx.store,
        seat,
        role=target.role,
        anchor=target.anchor,
        profile=target.profile,
        granted_by=target.granted_by,
        ttl_s=None,
    )
    path = agent_token_path(ctx.store)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(path, token.encode("utf-8"))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return {
        "enabled": True,
        "seat": seat,
        "role": target.role,
        "anchor": target.anchor,
        "expires": None,
        "credential": str(path),
        "note": "non-terminal CLI and MCP callers use this credential automatically",
    }


def disable(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Close the local credential and remove it; the seat grant stays live."""
    _human(ctx)
    token = _read(ctx.store)
    if token is None:
        return {"enabled": False}
    found = resolve_session(ctx.store, token)
    seat = None if found is None else found.seat
    if found is not None:
        close_sessions(ctx.store, session_id=found.id)
    with contextlib.suppress(OSError):
        agent_token_path(ctx.store).unlink()
    return {"enabled": False, "seat": seat}


def show(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Whether a local credential exists and which live seat it names."""
    token = _read(ctx.store)
    if token is None:
        return {"enabled": False}
    found = resolve_session(ctx.store, token)
    if found is None:
        return {"enabled": False, "reason": "credential_invalid"}
    target = query.live_seats(ctx.index).get(found.seat)
    return {
        "enabled": target is not None,
        "seat": found.seat,
        "role": found.role,
        "anchor": found.anchor,
        "expires": _stamp(found.expires),
        "live": target is not None,
    }


def _seat_name(ctx: Context, live: dict[str, Any], value: Any) -> str:
    """The named seat, or the only one this store can mean."""
    if value is not None:
        return _text(value, "seat")
    if len(live) == 1:
        return next(iter(live))
    roles = sorted(ctx.pack.operating.roles)
    if not live and len(roles) == 1:
        return roles[0]
    candidates = sorted(live) or roles
    raise WriteRefused(
        "VALIDATION_FAILED",
        "seat is required; candidates: " + ", ".join(candidates),
        {"reason": "seat_required", "seats": sorted(live), "roles": roles},
    )


def _role_name(ctx: Context, value: Any) -> str:
    """The named role, or the only role the pack declares."""
    if value is not None:
        return _text(value, "role")
    roles = sorted(ctx.pack.operating.roles)
    if len(roles) == 1:
        return roles[0]
    raise WriteRefused(
        "VALIDATION_FAILED",
        "role is required; this store declares: " + ", ".join(roles),
        {"reason": "role_required", "roles": roles},
    )


def _human(ctx: Context) -> None:
    if ctx.identity.kind != "human":
        raise WriteRefused(
            "PERMISSION_DENIED",
            "binding the local agent is human-only; run this in a terminal or the local UI",
            {"reason": "human_channel_required"},
        )


def _reset_old_credential(ctx: Context) -> None:
    token = _read(ctx.store)
    if token is None:
        return
    found = resolve_session(ctx.store, token)
    if found is not None:
        close_sessions(ctx.store, session_id=found.id)


def _read(store: Path) -> str | None:
    try:
        value = agent_token_path(store).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _stamp(expires: float | None) -> str | None:
    if expires is None:
        return None
    return datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"{key} must be non-empty text", {"reason": f"{key}_invalid"}
        )
    return value.strip()
