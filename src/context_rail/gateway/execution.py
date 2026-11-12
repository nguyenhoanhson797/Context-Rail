"""Gateway execution context: the explicit actor and the serving connection."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextvars import ContextVar

from context_rail.store.write import WriteRefused

EXPLICIT_ACTOR: ContextVar[str | None] = ContextVar("explicit_actor", default=None)
ACTOR_NAMED: ContextVar[bool] = ContextVar("actor_named", default=True)
SERVING: ContextVar[bool] = ContextVar("serving", default=False)
#: Set by a channel only a person can use (a terminal prompt, the UI's per-run
#: token); an agent's session never sets it.
HUMAN_CHANNEL: ContextVar[bool] = ContextVar("human_channel", default=False)
#: Whether this call arrived from an interactive terminal. An agent runner has
#: no TTY, so a tokenless agent can never claim to be the person.
HAS_TTY: ContextVar[bool] = ContextVar("has_tty", default=False)
#: The seat name a caller asks to view the store as; read-only once set.
LENS_SEAT: ContextVar[str | None] = ContextVar("lens_seat", default=None)
#: The caller's own ``RAIL_ACTOR``, held in a 1-tuple so "the caller named
#: nothing" differs from "nobody said": unset means read this process.
_CALLER_ACTOR_ENV: ContextVar[tuple[str | None] | None] = ContextVar(
    "caller_actor_env", default=None
)
#: The caller's session token, held the same way as the actor env.
_CALLER_SESSION: ContextVar[tuple[str | None] | None] = ContextVar(
    "caller_session", default=None
)


def _named(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@contextlib.contextmanager
def caller_identity(
    actor: str | None,
    env_actor: str | None,
    *,
    session: str | None = None,
    tty: bool = False,
    lens: str | None = None,
) -> Iterator[None]:
    """Run a call as the caller named it, not as this process's environment.

    The daemon outlives its callers and was started by one of them, so its own
    ``RAIL_ACTOR`` and ``RAIL_SESSION`` say nothing about who is asking. The
    terminal flag and the lens travel with the call the same way.
    """
    actor_token = EXPLICIT_ACTOR.set(actor)
    env_token = _CALLER_ACTOR_ENV.set((_named(env_actor),))
    session_token = _CALLER_SESSION.set((_named(session),))
    tty_token = HAS_TTY.set(bool(tty))
    lens_token = LENS_SEAT.set(_named(lens))
    try:
        yield
    finally:
        LENS_SEAT.reset(lens_token)
        HAS_TTY.reset(tty_token)
        _CALLER_SESSION.reset(session_token)
        _CALLER_ACTOR_ENV.reset(env_token)
        EXPLICIT_ACTOR.reset(actor_token)


def caller_actor_env() -> str | None:
    """The caller's ``RAIL_ACTOR``: the one a daemon was told, else this process's."""
    held = _CALLER_ACTOR_ENV.get()
    if held is not None:
        return held[0]
    return _named(os.environ.get("RAIL_ACTOR"))


def caller_session() -> str | None:
    """The caller's session token: the one a daemon was told, else this process's."""
    held = _CALLER_SESSION.get()
    if held is not None:
        return held[0]
    return _named(os.environ.get("RAIL_SESSION"))


@contextlib.contextmanager
def human_channel() -> Iterator[None]:
    """Mark the current call as arriving over a channel only a human can use."""
    token = HUMAN_CHANNEL.set(True)
    try:
        yield
    finally:
        HUMAN_CHANNEL.reset(token)


def resolve_actor_name(actor: str | None) -> tuple[str | None, bool]:
    """The name the caller gave (flag, then ``RAIL_ACTOR``), or ``None`` when unnamed.

    An unnamed caller is not assumed to be anyone: the identity layer decides
    from the channel whether it is the human or anonymous.
    """
    if isinstance(actor, str) and actor.strip():
        return actor, True
    from_env = caller_actor_env()
    if from_env is not None:
        return from_env, True
    return None, False


@contextlib.contextmanager
def serving_connection() -> Iterator[None]:
    """Mark the current call as handled by a live MCP connection."""
    token = SERVING.set(True)
    try:
        yield
    finally:
        SERVING.reset(token)


def resolve_server_actor(explicit: str | None) -> str:
    """The actor a serving process is pinned to: explicit, env, else ``cto``."""
    candidate = explicit
    if candidate is None:
        candidate = os.environ.get("RAIL_ACTOR")
    if candidate is None:
        candidate = "cto"
    if not isinstance(candidate, str) or not candidate.strip():
        raise WriteRefused("VALIDATION_FAILED", "an actor must be non-empty text")
    return candidate.strip()
