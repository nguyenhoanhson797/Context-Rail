"""Gateway execution context: the explicit actor and the serving connection."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextvars import ContextVar

from context_rail.store.write import WriteRefused

EXPLICIT_ACTOR: ContextVar[str | None] = ContextVar("explicit_actor", default=None)
SERVING: ContextVar[bool] = ContextVar("serving", default=False)


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
