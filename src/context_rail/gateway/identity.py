"""Identity: who a call acts as.

A store whose pack declares roles acts by seat, proven by a session token or
by a human channel. Any other store keeps the name-based actor it always had.
This module is the one place that turns an actor name into rights; the rest of
the gateway reads the resulting :class:`Identity`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from context_rail.context.reach import ReachError, resolve_reach
from context_rail.gateway.execution import resolve_actor_name
from context_rail.kernel.seats import LiveSeat
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.roles import Role
from context_rail.model.seats import HUMAN
from context_rail.store import query
from context_rail.store.location import PathEscape, find_store
from context_rail.store.sessions import Session, resolve_session
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = [
    "ACTOR_HINT",
    "ANONYMOUS",
    "Identity",
    "IdentityRefused",
    "name_is_cto",
    "name_is_human",
    "resolve_identity",
    "roles_declared",
]

ANONYMOUS = "anonymous"
_OPEN_COMMAND = "rail seat open <seat> --print-env"
ACTOR_HINT = "pass --actor <name> or set RAIL_ACTOR"
_CTO = "cto"
_LEAD_NAMES = (_CTO, HUMAN)


def name_is_human(name: str) -> bool:
    """Whether a name-only caller is the human.

    ``init`` and ``migrate`` run before a store has an identity to resolve, so
    they still read the name the gateway resolved for them.
    """
    return name == HUMAN


def name_is_cto(name: str) -> bool:
    """Whether a name-only caller is the CTO, for the store-less draft path."""
    return name == _CTO


def roles_declared(store: Path | None) -> bool:
    """Whether the store's pack declares roles, for a transport with no context.

    A server pins identity per request, so it must know whether to send an
    actor name or a session before the first call opens a context of its own.
    """
    if store is None:
        try:
            store = find_store(Path.cwd())
        except (PathEscape, OSError):
            return False
        if store is None:
            return False
    try:
        manifest = load_manifest(store)
        pack = load_pack(manifest.pack, store)
    except (ModelError, OSError, ValueError):
        return False
    return bool(getattr(pack.operating, "roles", None))


class IdentityRefused(WriteRefused):
    """A call whose caller could not be identified or may not use this channel."""


@dataclass(frozen=True)
class Identity:
    """The acting identity of one call.

    ``kind`` is ``legacy`` (a named actor in a store without roles), ``human``
    (the person on a terminal or the UI), ``seat`` (a session's seat) or
    ``anonymous`` (no proof at all). ``view_as`` is the seat a read-only lens
    shows. ``lead`` marks the caller that may run the project's loop.
    """

    kind: str
    name: str
    seat: LiveSeat | None = None
    role: Role | None = None
    view_as: LiveSeat | None = None
    human: bool = False
    lead: bool = False
    session: str | None = None

    def may(self, key: str) -> bool:
        """Whether the identity may use the ``can`` key (a write tool family)."""
        if self.kind in ("legacy", "human"):
            return True
        if self.kind == "seat" and self.role is not None:
            return bool(self.role.can.methods.get(key))
        return False


def resolve_identity(
    context: Context,
    *,
    actor: str | None,
    session: str | None,
    tty: bool = False,
    lens: str | None = None,
    human_channel: bool = False,
) -> Identity:
    """The identity a call acts as; refuses a caller it cannot place.

    ``actor`` is the name the caller passed (flag or parameter), if any. In role
    mode it is refused, and ``RAIL_ACTOR`` is never read. A terminal or the UI's
    per-run channel is the human only when no token is present. ``lens`` names a
    live seat the caller views the store as; a seat caller may only lens a seat
    whose anchor lies in its read reach.
    """
    if not context.role_mode:
        if lens is not None:
            raise IdentityRefused(
                "VALIDATION_FAILED",
                "this pack declares no roles; a seat lens needs a store with roles",
                {"reason": "no_roles", "command": "rail pack use <pack>"},
            )
        return _legacy(context, actor, terminal=tty, human_channel=human_channel)
    if isinstance(actor, str) and actor.strip():
        raise IdentityRefused(
            "PERMISSION_DENIED",
            "this store acts by seat: --actor is not accepted; "
            "open a session with 'rail seat open'",
            {"reason": "actor_flag_refused", "command": _OPEN_COMMAND},
        )
    if session is not None:
        identity = _seat(context, session)
    elif human_channel or tty:
        identity = Identity(kind="human", name=HUMAN, human=True, lead=True)
    else:
        identity = Identity(kind="anonymous", name=ANONYMOUS)
    if lens is None:
        return identity
    return _with_lens(context, identity, lens)


def _with_lens(context: Context, identity: Identity, name: str) -> Identity:
    """``identity`` viewing the live seat ``name``, refused when it may not."""
    if identity.kind == "seat" and name == identity.name:
        return identity
    live = query.live_seats(context.index)
    target = live.get(name)
    if target is None:
        raise IdentityRefused(
            "PERMISSION_DENIED",
            f"no live seat is named {name!r}",
            {"reason": "lens_unknown_seat", "seat": name, "candidates": sorted(live)},
        )
    if identity.kind == "seat" and not _can_read_anchor(context, identity, target.anchor):
        raise IdentityRefused(
            "PERMISSION_DENIED",
            f"seat {name!r} is outside the caller's read reach",
            {"reason": "lens_out_of_reach", "seat": name, "anchor": target.anchor},
        )
    return replace(identity, view_as=target)


def _can_read_anchor(context: Context, identity: Identity, anchor: str) -> bool:
    """Whether the caller's own seat may read the target seat's anchor."""
    if identity.role is None or identity.seat is None:
        return False
    try:
        reach = resolve_reach(context.index, context.pack, identity.role, identity.seat)
    except ReachError as exc:
        raise IdentityRefused(
            "PERMISSION_DENIED",
            f"the caller's reach cannot be resolved: {exc}",
            {"reason": "lens_out_of_reach", "anchor": anchor},
        ) from exc
    row = context.index.conn.execute(
        "SELECT path, preset FROM docs WHERE slug = ?", (anchor,)
    ).fetchone()
    if row is None:
        return reach.read_all
    return reach.can_read(anchor, path=row[0], preset=row[1])


def _legacy(
    context: Context, actor: str | None, *, terminal: bool, human_channel: bool
) -> Identity:
    """A name in a store without roles; an unnamed caller is the human only on a person's channel."""
    name, _ = resolve_actor_name(actor)
    if name is None:
        if not (terminal or human_channel):
            return Identity(kind="anonymous", name=ANONYMOUS)
        name = HUMAN
    return Identity(
        kind="legacy",
        name=name,
        human=name == HUMAN,
        lead=name in _LEAD_NAMES,
    )


def _seat(context: Context, token: str) -> Identity:
    found = resolve_session(context.store, token)
    if found is None:
        raise _invalid("the session is unknown, expired or closed")
    live = query.live_seats(context.index)
    seat = live.get(found.seat)
    if seat is None:
        raise _invalid(f"seat {found.seat!r} is not live")
    if _changed(found, seat) or _opened_before_grant(found, seat.granted_at):
        raise _invalid(f"seat {found.seat!r} was granted again after this session opened")
    role = context.pack.operating.roles.get(seat.role)
    if role is None:
        raise IdentityRefused(
            "PERMISSION_DENIED",
            f"seat {seat.seat!r} holds role {seat.role!r}, which the pack no longer declares",
            {"reason": "seat_role_unknown", "seat": seat.seat, "role": seat.role},
        )
    return Identity(
        kind="seat",
        name=seat.seat,
        seat=seat,
        role=role,
        lead=role.anchor.kind == "project",
        session=found.id,
    )


def _opened_before_grant(found: Session, granted_at: str) -> bool:
    """Whether the session predates the live grant that now carries its name."""
    try:
        granted = datetime.strptime(granted_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return True
    return int(found.created) < int(granted.timestamp())


def _changed(found: Session, seat: LiveSeat) -> bool:
    """Whether the live grant differs from what the session was opened for."""
    opened = (found.role, found.anchor, found.profile)
    if opened == (None, None, None):
        return False
    return opened != (seat.role, seat.anchor, seat.profile)


def _invalid(message: str) -> IdentityRefused:
    return IdentityRefused(
        "PERMISSION_DENIED",
        message,
        {"reason": "session_invalid", "command": _OPEN_COMMAND},
    )
