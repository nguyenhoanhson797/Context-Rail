"""Code graph providers: each turns a committed tree into one normalized snapshot.

``ensure_snapshot`` is the one entry point the rest of the rail uses. It never
fails a caller over a slow or broken provider; it reports a warning instead.
The one refusal is a provider that changed the working tree.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from context_rail.model.codegraph import PROVIDERS, Snapshot
from context_rail.runner.codegraph import builtin
from context_rail.runner.codegraph.provider import (
    BUILTIN_TIMEOUT,
    EXTERNAL_TIMEOUT,
    ExtractionError,
)
from context_rail.runner.git import status_porcelain
from context_rail.store.codegraph import (
    has_snapshot,
    last_event,
    provider_dir,
    read_readiness,
    record_event,
    save_snapshot,
    snapshot_info,
)
from context_rail.store.write import WriteRefused

__all__ = ["Ensured", "ensure_snapshot", "readiness"]

_EXTERNAL = tuple(provider for provider in PROVIDERS if provider != "builtin")


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
    timeout_s: float | None = None,
    protect: Iterable[str] = (),
    force: bool = False,
) -> Ensured:
    """Make sure ``commit`` has a cached snapshot, building it when it has none.

    ``configured`` is the resolved provider choice. A cache hit costs one
    lookup. An external provider falls back to ``builtin``. A timeout or a
    failure returns a warning and no provider. A provider that leaves the
    working tree changed raises ``WriteRefused``.
    """
    if configured == "none":
        return Ensured(commit, None, False, None, None)
    timeout = _timeout(configured, timeout_s)
    fallback = configured if configured in _EXTERNAL else None

    if not force:
        cached = _cached(store, commit, configured)
        if cached is not None:
            return cached

    worktree = Path(worktree)
    before = set(status_porcelain(worktree, worktree))
    reason: str | None = None
    problem: tuple[str, str] | None = None

    if configured == "builtin":
        snapshot, problem = _builtin(worktree, commit, timeout)
    else:
        snapshot, reason, problem = _external(
            store, worktree, commit, configured, timeout
        )

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
    if fallback is not None and snapshot.provider == "builtin":
        reason = reason or "the configured provider did not produce a snapshot"
        record_event(store, "fallback", {"configured": fallback, "reason": reason})
        return Ensured(commit, "builtin", True, fallback, reason)
    record_event(store, "built", {"commit": commit, "provider": snapshot.provider})
    return Ensured(commit, snapshot.provider, True, None, None)


def readiness(store: Path, configured: str) -> dict[str, Any]:
    """Whether ``configured`` can serve graphs right now, with the line to show."""
    from context_rail.runner.codegraph.probe import readiness as cached

    return cached(store, configured)


def _timeout(configured: str, timeout_s: float | None) -> float:
    if timeout_s is not None:
        return float(timeout_s)
    return EXTERNAL_TIMEOUT if configured in _EXTERNAL else BUILTIN_TIMEOUT


def _cached(store: Path, commit: str, configured: str) -> Ensured | None:
    """The answer a cache hit gives, or None when a build is still needed."""
    if not has_snapshot(store, commit):
        return None
    info = snapshot_info(store, commit)
    if info is None:
        return None
    stored = info["provider"]
    if stored == configured:
        return Ensured(commit, stored, False, None, None)
    if stored == "builtin" and configured in _EXTERNAL:
        return Ensured(
            commit,
            "builtin",
            False,
            configured,
            _fallback_reason(store, configured),
        )
    return None


def _external(
    store: Path,
    worktree: Path,
    commit: str,
    configured: str,
    timeout: float,
) -> tuple[Snapshot | None, str | None, tuple[str, str] | None]:
    """Try the configured adapter, then the builtin one when it cannot serve."""
    try:
        adapter = _load_adapter(configured)
        snapshot = adapter.extract(
            worktree,
            commit,
            cache_dir=provider_dir(store, configured),
            timeout_s=timeout,
        )
        _validate(snapshot, configured, commit)
        return snapshot, None, None
    except TimeoutError:
        reason = f"{configured} extraction timed out after {timeout:g} s"
    except ExtractionError as exc:
        reason = exc.message
    except Exception as exc:  # noqa: BLE001 - the graph is advisory; a bug in it must not stop a dispatch
        reason = f"{configured} extraction failed: {exc}"

    snapshot, problem = _builtin(worktree, commit, BUILTIN_TIMEOUT)
    return snapshot, reason, problem


def _builtin(
    worktree: Path, commit: str, timeout: float
) -> tuple[Snapshot | None, tuple[str, str] | None]:
    try:
        return (
            builtin.extract(worktree, commit, deadline=time.monotonic() + timeout),
            None,
        )
    except TimeoutError:
        return None, ("timeout", f"code graph extraction timed out after {timeout:g} s")
    except Exception as exc:  # noqa: BLE001 - the graph is advisory; a bug in it must not stop a dispatch
        return None, ("unavailable", f"code graph extraction failed: {exc}")


def _load_adapter(provider: str) -> Any:
    """Import one adapter lazily; no external module loads with the CLI."""
    return import_module(f"context_rail.runner.codegraph.{provider}")


def _validate(snapshot: Snapshot, configured: str, commit: str) -> None:
    if snapshot.provider != configured:
        raise ExtractionError(
            "invalid",
            f"{configured} returned a snapshot from {snapshot.provider}",
        )
    if snapshot.commit != commit:
        raise ExtractionError(
            "invalid",
            f"{configured} returned a snapshot for {snapshot.commit}, not {commit}",
        )


def _fallback_reason(store: Path, configured: str) -> str:
    event = last_event(store)
    if event is not None and event.get("kind") == "fallback":
        detail = event.get("detail") or {}
        if detail.get("configured") == configured:
            return str(
                detail.get("reason")
                or detail.get("message")
                or f"{configured} is not available"
            )
    record = read_readiness(store).get(configured)
    if isinstance(record, dict) and not record.get("ready", False):
        return str(
            record.get("fix")
            or record.get("line")
            or f"{configured} is not available"
        )
    return f"{configured} is not available"
