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
#: The caller's own ``RAIL_ACTOR``, held in a 1-tuple so "the caller named
#: nothing" differs from "nobody said": unset means read this process.
_CALLER_ACTOR_ENV: ContextVar[tuple[str | None] | None] = ContextVar(
    "caller_actor_env", default=None
)


def _named(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@contextlib.contextmanager
def caller_identity(actor: str | None, env_actor: str | None) -> Iterator[None]:
    """Run a call as the caller named it, not as this process's environment.

    The daemon outlives its callers and was started by one of them, so its own
    ``RAIL_ACTOR`` says nothing about who is asking.
    """
    actor_token = EXPLICIT_ACTOR.set(actor)
    env_token = _CALLER_ACTOR_ENV.set((_named(env_actor),))
    try:
        yield
    finally:
        _CALLER_ACTOR_ENV.reset(env_token)
        EXPLICIT_ACTOR.reset(actor_token)


def caller_actor_env() -> str | None:
    """The caller's ``RAIL_ACTOR``: the one a daemon was told, else this process's."""
    held = _CALLER_ACTOR_ENV.get()
    if held is not None:
        return held[0]
    return _named(os.environ.get("RAIL_ACTOR"))


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
