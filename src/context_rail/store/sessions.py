"""Hashed session files: who a caller is, kept only as a token hash.

A session lives in the store's local directory beside the index, never in the
store itself. The token is handed back once and never written; the file name
and the ``id`` field are its SHA-256, so reading the folder reveals no
credential. A file that cannot be read back exactly, or whose ``id`` is not its
own name, resolves to nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from context_rail.core.util import write_bytes_atomic
from context_rail.store.location import local_dir

__all__ = [
    "DEFAULT_TTL_S",
    "SESSIONS_DIR",
    "Session",
    "close_sessions",
    "close_all_sessions",
    "drop_expired_sessions",
    "open_session",
    "resolve_session",
]

SESSIONS_DIR = "sessions"
DEFAULT_TTL_S = 43200
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}")


@dataclass(frozen=True)
class Session:
    """One live identity: ``id`` is the SHA-256 hex of the secret token."""

    id: str
    seat: str
    run: str | None
    role: str | None
    anchor: str | None
    profile: str | None
    granted_by: str | None
    created: float
    expires: float | None


def open_session(
    store: Path,
    seat: str,
    *,
    run: str | None = None,
    role: str | None = None,
    anchor: str | None = None,
    profile: str | None = None,
    granted_by: str | None = None,
    ttl_s: int | None = DEFAULT_TTL_S,
    now: float | None = None,
) -> tuple[str, Session]:
    """Write a session file and return ``(token, session)``.

    The token appears only in the return value. ``ttl_s=None`` never expires.
    """
    created = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    session = Session(
        id=_token_id(token),
        seat=seat,
        run=run,
        role=role,
        anchor=anchor,
        profile=profile,
        granted_by=granted_by,
        created=created,
        expires=None if ttl_s is None else created + ttl_s,
    )
    folder = _folder(store)
    folder.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(folder, 0o700)
    path = folder / f"{session.id}.json"
    payload = json.dumps(asdict(session), sort_keys=True).encode("utf-8")
    write_bytes_atomic(path, payload)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return token, session


def resolve_session(
    store: Path, token: str, *, now: float | None = None
) -> Session | None:
    """The live session for ``token``, or None. An expired file is removed."""
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    session_id = _token_id(token)
    path = _folder(store) / f"{session_id}.json"
    session = _load(path)
    if session is None or session.id != session_id:
        return None
    if _expired(session, time.time() if now is None else now):
        # A read never writes; the expired file is removed by a write-side
        # cleanup such as seat open, seat close or drop_expired_sessions.
        return None
    return session


def close_sessions(
    store: Path,
    *,
    seat: str | None = None,
    run: str | None = None,
    session_id: str | None = None,
) -> int:
    """Remove every session matching all given filters; return how many.

    Closing needs at least one filter, so no call can close every session.
    """
    if seat is None and run is None and session_id is None:
        raise ValueError("close_sessions needs a seat, a run or a session id")
    closed = 0
    for path, session in _entries(store):
        if seat is not None and session.seat != seat:
            continue
        if run is not None and session.run != run:
            continue
        if session_id is not None and session.id != session_id:
            continue
        closed += _remove(path)
    return closed


def drop_expired_sessions(store: Path, *, now: float | None = None) -> int:
    """Remove every expired session; return how many."""
    moment = time.time() if now is None else now
    return sum(
        _remove(path) for path, session in _entries(store) if _expired(session, moment)
    )


def close_all_sessions(store: Path) -> int:
    """Remove every session, manual or run; only the human ``seat close --all`` uses it."""
    return sum(_remove(path) for path, _session in _entries(store))


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _folder(store: Path) -> Path:
    return local_dir(store) / SESSIONS_DIR


def _expired(session: Session, now: float) -> bool:
    return session.expires is not None and now >= session.expires


def _remove(path: Path) -> int:
    try:
        path.unlink()
    except FileNotFoundError:
        return 0
    return 1


def _entries(store: Path) -> Iterator[tuple[Path, Session]]:
    folder = _folder(store)
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        session = _load(path)
        if session is not None and session.id == path.stem:
            yield path, session


def _load(path: Path) -> Session | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return _session(raw)
    except (KeyError, TypeError):
        return None


def _session(raw: dict[str, Any]) -> Session:
    if set(raw) != set(Session.__dataclass_fields__):
        raise TypeError("unexpected session keys")
    text = ("id", "seat")
    optional_text = ("run", "role", "anchor", "profile", "granted_by")
    for key in text:
        if not isinstance(raw[key], str) or not raw[key]:
            raise TypeError(key)
    for key in optional_text:
        if raw[key] is not None and not isinstance(raw[key], str):
            raise TypeError(key)
    if not _number(raw["created"]):
        raise TypeError("created")
    if raw["expires"] is not None and not _number(raw["expires"]):
        raise TypeError("expires")
    return Session(**raw)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
