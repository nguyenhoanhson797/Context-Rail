"""Code graph providers: each turns a committed tree into one normalized snapshot.

``ensure_snapshot`` is the one entry point the rest of the rail uses. It never
fails a caller over a slow or broken provider; it reports a warning instead.
The one refusal is a provider that changed the working tree.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.model.codegraph import PROVIDERS
from context_rail.runner.codegraph import builtin
from context_rail.runner.git import status_porcelain
from context_rail.store.codegraph import (
    has_snapshot,
    latest_commit,
    record_event,
    save_snapshot,
    snapshot_info,
)
from context_rail.store.write import WriteRefused

__all__ = ["Ensured", "ensure_snapshot", "readiness"]

_EXTERNAL = tuple(provider for provider in PROVIDERS if provider != "builtin")
_NO_ADAPTER = "no adapter ships in this release; builtin is used instead"


@dataclass(frozen=True)
class Ensured:
    """What ``ensure_snapshot`` did for one commit."""

    commit: str
    provider: str | None
    built: bool
    fallback: str | None
    warning: str | None


def ensure_snapshot(
    store: Path,
    *,
    worktree: Path,
    commit: str,
    configured: str,
    timeout_s: float = 30.0,
    protect: Iterable[str] = (),
) -> Ensured:
    """Make sure ``commit`` has a cached snapshot, building it when it has none.

    ``configured`` is the resolved provider choice. A cache hit costs one
    lookup. An external provider falls back to ``builtin``. A timeout or a
    failure returns a warning and no provider. A provider that leaves the
    working tree changed raises ``WriteRefused``.
    """
    if configured == "none":
        return Ensured(commit, None, False, None, None)
    fallback = configured if configured in _EXTERNAL else None
    if has_snapshot(store, commit):
        info = snapshot_info(store, commit)
        return Ensured(commit, None if info is None else info["provider"], False, fallback, None)

    worktree = Path(worktree)
    before = set(status_porcelain(worktree, worktree))
    snapshot = None
    problem: tuple[str, str] | None = None
    try:
        snapshot = builtin.extract(worktree, commit, deadline=time.monotonic() + timeout_s)
    except TimeoutError:
        problem = ("timeout", f"code graph extraction timed out after {timeout_s:g} s")
    except Exception as exc:  # noqa: BLE001 - the graph is advisory; a bug in it must not stop a dispatch
        problem = ("unavailable", f"code graph extraction failed: {exc}")

    stray = sorted(set(status_porcelain(worktree, worktree)) - before)
    if stray:
        paths = [entry[3:] for entry in stray]
        record_event(store, "dirty_tree", {"paths": paths})
        raise WriteRefused(
            "PROCESS_ERROR",
            "the code graph provider changed the working tree: " + ", ".join(paths),
            {"reason": "code_graph_dirty_tree", "paths": paths},
        )
    if snapshot is None:
        kind, warning = problem or ("unavailable", "code graph extraction produced nothing")
        record_event(store, kind, {"configured": configured, "message": warning})
        return Ensured(commit, None, False, fallback, warning)

    save_snapshot(store, snapshot, protect=protect)
    if fallback is not None:
        record_event(store, "fallback", {"configured": fallback, "reason": _NO_ADAPTER})
    else:
        record_event(store, "built", {"commit": commit, "provider": snapshot.provider})
    return Ensured(commit, snapshot.provider, True, fallback, None)


def readiness(store: Path, configured: str) -> dict[str, Any]:
    """Whether ``configured`` can serve graphs right now, with the line to show."""
    if configured == "none":
        return _ready(False, "none", None, None, None, "code graph: off")
    if configured in _EXTERNAL:
        fix = _NO_ADAPTER
        return _ready(
            False,
            configured,
            "builtin",
            "binary",
            fix,
            f"code graph: not ready: binary: {fix} (fallback: builtin)",
        )
    newest = latest_commit(store)
    info = None if newest is None else snapshot_info(store, newest)
    if newest is None or info is None:
        size = "no snapshot yet"
    else:
        size = f"{info['nodes']} nodes @{newest[:7]}"
    return _ready(
        True,
        "builtin",
        "builtin",
        None,
        None,
        f"code graph: ready (builtin {builtin.BUILTIN_VERSION}, {size})",
    )


def _ready(
    ready: bool,
    provider: str,
    used: str | None,
    step: str | None,
    fix: str | None,
    line: str,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "provider": provider,
        "used": used,
        "step": step,
        "fix": fix,
        "line": line,
    }
