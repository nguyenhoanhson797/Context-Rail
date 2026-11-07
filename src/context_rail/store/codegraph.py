"""The code graph cache: one snapshot per commit, kept outside the repository.

The cache is rebuildable, so it never goes through ``store.write.commit``. It
is one SQLite file under the store's local directory. A snapshot's row is the
last thing a save inserts, inside the same transaction as its nodes and edges,
so a reader that finds the row finds the whole commit.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

from context_rail.core.locate import head_sha, local_dir
from context_rail.model.codegraph import Edge, Node, Snapshot

__all__ = [
    "KEEP_COMMITS",
    "CachedGraph",
    "cache_path",
    "freshness",
    "has_snapshot",
    "last_event",
    "latest_commit",
    "open_graph",
    "record_event",
    "save_snapshot",
    "snapshot_info",
]

KEEP_COMMITS = 3
EVENT_KINDS = ("built", "fallback", "unavailable", "invalid", "dirty_tree", "timeout")

_SCHEMA_VERSION = 1
_BUSY_SECONDS = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sha TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    built_at TEXT NOT NULL,
    calls INTEGER NOT NULL,
    lines INTEGER NOT NULL,
    files_listed INTEGER NOT NULL,
    files_parsed INTEGER NOT NULL,
    skipped TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS node (
    sha TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    file TEXT NOT NULL,
    start INTEGER,
    "end" INTEGER,
    PRIMARY KEY (sha, id)
);
CREATE INDEX IF NOT EXISTS node_file ON node (sha, file);
CREATE INDEX IF NOT EXISTS node_name ON node (sha, name);
CREATE TABLE IF NOT EXISTS edge (
    sha TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    rel TEXT NOT NULL,
    confidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edge_dst ON edge (sha, dst);
CREATE INDEX IF NOT EXISTS edge_src ON edge (sha, src);
"""


def cache_path(store: Path) -> Path:
    """Where the snapshots live: under the store's local directory."""
    return local_dir(store) / "codegraph" / "graph.sqlite"


def save_snapshot(
    store: Path, snapshot: Snapshot, *, protect: Iterable[str] = ()
) -> None:
    """Write ``snapshot`` and drop commits beyond the newest few.

    Every commit named in ``protect`` survives retention. A cache file that
    cannot be read is replaced.
    """
    path = cache_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _save(path, snapshot, set(protect))
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
            raise
        _discard(path)
        _save(path, snapshot, set(protect))


def has_snapshot(store: Path, commit: str) -> bool:
    return _row(store, "SELECT 1 FROM snapshot WHERE sha = ?", (commit,)) is not None


def open_graph(store: Path, commit: str) -> CachedGraph | None:
    """The graph of ``commit``, or None when it is not cached or unreadable."""
    path = cache_path(store)
    if not path.is_file():
        return None
    try:
        conn = _connect_readonly(path)
    except sqlite3.DatabaseError:
        record_event(store, "invalid", {"reason": "cache file cannot be opened"})
        return None
    try:
        found = conn.execute(
            "SELECT 1 FROM snapshot WHERE sha = ?", (commit,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        conn.close()
        if "no such table" in str(exc):
            return None
        record_event(store, "invalid", {"reason": str(exc)})
        return None
    except sqlite3.DatabaseError as exc:
        conn.close()
        record_event(store, "invalid", {"reason": str(exc)})
        return None
    if found is None:
        conn.close()
        return None
    return CachedGraph(conn, commit)


def snapshot_info(store: Path, commit: str) -> dict[str, Any] | None:
    """Provider, version, coverage and size of one cached snapshot."""
    row = _row(
        store,
        "SELECT provider, provider_version, built_at, calls, lines, files_listed, "
        "files_parsed, skipped, node_count, edge_count FROM snapshot WHERE sha = ?",
        (commit,),
    )
    if row is None:
        return None
    return {
        "commit": commit,
        "provider": row[0],
        "provider_version": row[1],
        "built_at": row[2],
        "calls": bool(row[3]),
        "lines": bool(row[4]),
        "files_listed": row[5],
        "files_parsed": row[6],
        "skipped": json.loads(row[7]),
        "nodes": row[8],
        "edges": row[9],
    }


def latest_commit(store: Path) -> str | None:
    row = _row(store, "SELECT sha FROM snapshot ORDER BY seq DESC LIMIT 1", ())
    return None if row is None else str(row[0])


def freshness(store: Path) -> dict[str, Any]:
    """``fresh`` when the checkout's HEAD has a snapshot, else ``stale`` or ``none``."""
    head = head_sha(store)
    if head is not None and has_snapshot(store, head):
        return {"state": "fresh", "graph": head, "head": head}
    newest = latest_commit(store)
    if newest is None:
        return {"state": "none", "graph": None, "head": head}
    return {"state": "stale", "graph": newest, "head": head}


def record_event(store: Path, kind: str, detail: dict[str, Any]) -> None:
    """Remember the latest thing that happened to the graph, for doctor and status."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown code graph event: {kind!r}")
    path = _event_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kind": kind, "detail": detail, "at": time.time()}
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def last_event(store: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(_event_path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


class CachedGraph:
    """One commit's graph, read from the cache through the neighbor protocol."""

    def __init__(self, conn: sqlite3.Connection, commit: str) -> None:
        self._conn = conn
        self.commit = commit

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def node(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            'SELECT id, kind, name, file, start, "end" FROM node WHERE sha = ? AND id = ?',
            (self.commit, node_id),
        ).fetchone()
        return None if row is None else Node(*row)

    def by_file(self, path: str) -> tuple[Node, ...]:
        return self._nodes("file", path)

    def by_name(self, name: str) -> tuple[Node, ...]:
        return self._nodes("name", name)

    def incoming(self, node_id: str) -> tuple[Edge, ...]:
        return self._edges("dst", node_id)

    def outgoing(self, node_id: str) -> tuple[Edge, ...]:
        return self._edges("src", node_id)

    def files(self) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT id FROM node WHERE sha = ? AND kind = 'file' ORDER BY id",
            (self.commit,),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _nodes(self, column: str, value: str) -> tuple[Node, ...]:
        rows = self._conn.execute(
            'SELECT id, kind, name, file, start, "end" FROM node '
            f"WHERE sha = ? AND {column} = ? ORDER BY id",
            (self.commit, value),
        ).fetchall()
        return tuple(Node(*row) for row in rows)

    def _edges(self, column: str, value: str) -> tuple[Edge, ...]:
        rows = self._conn.execute(
            "SELECT src, dst, rel, confidence FROM edge "
            f"WHERE sha = ? AND {column} = ? ORDER BY src, dst, rel",
            (self.commit, value),
        ).fetchall()
        return tuple(Edge(*row) for row in rows)


def _save(path: Path, snapshot: Snapshot, protect: set[str]) -> None:
    conn = sqlite3.connect(path, timeout=_BUSY_SECONDS, isolation_level=None)
    try:
        # A lost cache is rebuilt, so durability is not worth an fsync per save.
        conn.execute("PRAGMA synchronous = OFF")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SCHEMA_VERSION):
            raise sqlite3.DatabaseError("code graph cache has another schema version")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _create(conn)
            sha = snapshot.commit
            _drop(conn, sha)
            conn.executemany(
                "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (sha, n.id, n.kind, n.name, n.file, n.start, n.end)
                    for n in snapshot.nodes
                ),
            )
            conn.executemany(
                "INSERT INTO edge VALUES (?, ?, ?, ?, ?)",
                ((sha, e.src, e.dst, e.rel, e.confidence) for e in snapshot.edges),
            )
            conn.execute(
                "INSERT INTO snapshot (sha, provider, provider_version, built_at, calls, "
                "lines, files_listed, files_parsed, skipped, node_count, edge_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sha,
                    snapshot.provider,
                    snapshot.provider_version,
                    snapshot.built_at,
                    int(snapshot.calls),
                    int(snapshot.lines),
                    snapshot.files_listed,
                    snapshot.files_parsed,
                    json.dumps(list(snapshot.skipped)),
                    len(snapshot.nodes),
                    len(snapshot.edges),
                ),
            )
            _retain(conn, protect | {sha})
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _create(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _drop(conn: sqlite3.Connection, sha: str) -> None:
    for table in ("node", "edge", "snapshot"):
        conn.execute(f"DELETE FROM {table} WHERE sha = ?", (sha,))


def _retain(conn: sqlite3.Connection, protect: set[str]) -> None:
    rows = conn.execute("SELECT sha FROM snapshot ORDER BY seq DESC").fetchall()
    newest = {row[0] for row in rows[:KEEP_COMMITS]}
    for (sha,) in rows:
        if sha not in newest and sha not in protect:
            _drop(conn, sha)


def _discard(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            Path(str(path) + suffix).unlink()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=_BUSY_SECONDS)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _row(store: Path, sql: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
    path = cache_path(store)
    if not path.is_file():
        return None
    try:
        with contextlib.closing(_connect_readonly(path)) as conn:
            row = conn.execute(sql, params).fetchone()
    except sqlite3.DatabaseError:
        return None
    return None if row is None else tuple(row)


def _event_path(store: Path) -> Path:
    return local_dir(store) / "codegraph" / "event.json"
