"""The per-worktree SQLite index: a rebuildable cache of store documents.

The database lives in ``local_dir`` (the worktree's git dir, or
``<store>/.rail-local``) and runs in WAL mode so a reader never waits on the
writer. ``sync`` compares each file's persisted ``(mtime_ns, size)`` with the
stored row and re-parses only the files that changed, so an unchanged store
costs one stat per document and no hashing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_rail.core.util import sha256_hex
from context_rail.model.pack import Pack
from context_rail.store.doc import (
    BAD_ENCODING,
    PARSE_ERROR,
    Doc,
    doc_digest,
    parse_doc,
)
from context_rail.store.location import index_path, local_dir
from context_rail.store.lock import file_lock

__all__ = ["INDEX_SCHEMA", "Index", "IndexBusy", "SyncStats", "pack_hash"]

INDEX_SCHEMA = 1
EXCERPT_CAP = 500
FTS_BODY_CAP = 20000
BUSY_TIMEOUT_MS = 5000
BUSY_RETRIES = 2
CACHE_LOCK = "index.lock"

_DOC_SUFFIXES = (".md", ".json")
_UNCOUNTED_NAMES = frozenset({"rail.yaml", ".gitignore"})
_UNCOUNTED_DIRS = frozenset({"templates", "packs"})
_BROKEN_PROBLEMS = frozenset({PARSE_ERROR, BAD_ENCODING})

_SCHEMA = (
    """
    CREATE TABLE docs(
        slug TEXT NOT NULL,
        path TEXT PRIMARY KEY,
        primitive TEXT,
        preset TEXT,
        title TEXT,
        status TEXT,
        category TEXT,
        etag TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL,
        created TEXT,
        updated TEXT,
        excerpt TEXT NOT NULL,
        problems TEXT NOT NULL,
        digest TEXT,
        record_at INTEGER,
        broken INTEGER NOT NULL
    )
    """,
    "CREATE INDEX docs_slug ON docs(slug)",
    """
    CREATE TABLE fields(
        path TEXT NOT NULL,
        key TEXT NOT NULL,
        value_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE relations(
        src_path TEXT NOT NULL,
        src TEXT NOT NULL,
        rel TEXT NOT NULL,
        dst TEXT NOT NULL
    )
    """,
    "CREATE INDEX relations_dst ON relations(dst, rel)",
    "CREATE INDEX relations_src ON relations(src, rel)",
    "CREATE TABLE tags(path TEXT NOT NULL, tag TEXT NOT NULL)",
    "CREATE INDEX tags_tag ON tags(tag)",
    "CREATE VIRTUAL TABLE docs_fts USING fts5(slug, title, body, path UNINDEXED)",
    "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

_COLUMNS = (
    "slug, path, primitive, preset, title, status, category, etag, mtime_ns, "
    "size, created, updated, excerpt, problems, digest, record_at, broken"
)

_UPDATE_SET = ", ".join(
    f"{name.strip()} = excluded.{name.strip()}"
    for name in _COLUMNS.split(",")
    if name.strip() != "path"
)


@dataclass(frozen=True)
class SyncStats:
    """What one sync pass did, in documents and in files it refused to parse."""

    added: int
    changed: int
    removed: int
    skipped: int


class IndexBusy(Exception):
    """SQLite stayed locked for the whole retry budget: infrastructure, not corruption."""


class Index:
    """One open connection to a store's index database."""

    def __init__(
        self,
        store: Path,
        pack: Pack,
        conn: sqlite3.Connection,
        *,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    ) -> None:
        self.store = Path(store)
        self.pack = pack
        self.conn = conn
        self.busy_timeout_ms = busy_timeout_ms

    @classmethod
    def open(
        cls,
        store: Path,
        pack: Pack,
        *,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    ) -> "Index":
        """Open the index, creating or rebuilding it when absent or unusable.

        Replacing the file is serialised across processes with a cache lock in
        ``local_dir``; no document is parsed here, so the first ``sync`` counts
        every document as added.
        """
        store = Path(store)
        target = index_path(store)
        target.parent.mkdir(parents=True, exist_ok=True)
        wanted = pack_hash(pack)
        with file_lock(local_dir(store) / CACHE_LOCK):
            conn: sqlite3.Connection | None = None
            healthy = False
            try:
                conn = _connect(target, busy_timeout_ms)
                healthy = _matches(conn, wanted)
            except sqlite3.DatabaseError as exc:
                if _is_busy(exc):
                    raise IndexBusy(f"the index at {target} stayed locked") from exc
            if healthy and conn is not None:
                return cls(store, pack, conn, busy_timeout_ms=busy_timeout_ms)
            if conn is not None:
                conn.close()
            _discard(target)
            conn = _connect(target, busy_timeout_ms)
            try:
                _transaction(conn, lambda link: _create(link, wanted))
            except BaseException:
                conn.close()
                raise
            return cls(store, pack, conn, busy_timeout_ms=busy_timeout_ms)

    def sync(self) -> SyncStats:
        """Bring the index in line with the store, re-parsing only changes."""
        seen, skipped = self._scan()
        stored = {
            path: (mtime_ns, size)
            for path, mtime_ns, size in self.conn.execute(
                "SELECT path, mtime_ns, size FROM docs"
            )
        }
        added = sorted(path for path in seen if path not in stored)
        changed = sorted(
            path for path in seen if path in stored and stored[path] != seen[path]
        )
        removed = sorted(path for path in stored if path not in seen)
        if added or changed or removed:
            _transaction(
                self.conn,
                lambda link: self._apply(link, seen, added, changed, removed),
            )
        return SyncStats(len(added), len(changed), len(removed), skipped)

    def rebuild(self) -> SyncStats:
        """Drop every indexed row and sync the store from scratch."""

        def clear(conn: sqlite3.Connection) -> None:
            for table in ("docs", "fields", "relations", "tags", "docs_fts"):
                conn.execute(f"DELETE FROM {table}")

        with file_lock(local_dir(self.store) / CACHE_LOCK):
            _transaction(self.conn, clear)
        return self.sync()

    def close(self) -> None:
        """Close the connection, releasing the database file."""
        self.conn.close()

    def _scan(self) -> tuple[dict[str, tuple[int, int]], int]:
        """Every indexable file with its stat, plus the count of skipped ones."""
        root = self.store.resolve()
        seen: dict[str, tuple[int, int]] = {}
        skipped = 0
        if not root.is_dir():
            return seen, skipped
        stack: list[tuple[Path, str]] = [(root, "")]
        while stack:
            directory, prefix = stack.pop()
            with os.scandir(directory) as handle:
                entries = sorted(handle, key=lambda entry: entry.name)
            for entry in entries:
                rel = prefix + entry.name
                if entry.is_symlink():
                    if not _inside(root, entry) or entry.is_dir():
                        continue
                    if not entry.is_file():
                        continue
                elif entry.is_dir():
                    if entry.name.startswith(".") or entry.name in _UNCOUNTED_DIRS:
                        continue
                    stack.append((Path(entry.path), rel + "/"))
                    continue
                elif not entry.is_file():
                    continue
                if entry.name in _UNCOUNTED_NAMES or entry.name.lower().endswith(".tmp"):
                    continue
                if not entry.name.lower().endswith(_DOC_SUFFIXES):
                    skipped += 1
                    continue
                stat = entry.stat()
                seen[rel] = (stat.st_mtime_ns, stat.st_size)
        return seen, skipped

    def _apply(
        self,
        conn: sqlite3.Connection,
        seen: Mapping[str, tuple[int, int]],
        added: list[str],
        changed: list[str],
        removed: list[str],
    ) -> None:
        for path in removed:
            row = conn.execute("SELECT rowid FROM docs WHERE path = ?", (path,)).fetchone()
            if row is not None:
                self._clear(conn, path, row[0])
            conn.execute("DELETE FROM docs WHERE path = ?", (path,))
        for path in [*added, *changed]:
            mtime_ns, size = seen[path]
            data = (self.store / path).read_bytes()
            doc = parse_doc(path, data)
            self._store(conn, doc, data, mtime_ns, size)

    def _store(
        self,
        conn: sqlite3.Connection,
        doc: Doc,
        data: bytes,
        mtime_ns: int,
        size: int,
    ) -> None:
        broken = bool(_BROKEN_PROBLEMS.intersection(doc.problems))
        category = self.pack.statuses.get(doc.status) if doc.status else None
        record_at = _record_at(doc)
        values = (
            doc.slug,
            doc.path,
            doc.primitive,
            doc.preset,
            doc.title,
            doc.status,
            category,
            doc.etag,
            mtime_ns,
            size,
            doc.created,
            doc.updated,
            doc.body[:EXCERPT_CAP],
            _json(list(doc.problems)),
            doc_digest(doc.path, data),
            record_at,
            1 if broken else 0,
        )
        conn.execute(
            f"INSERT INTO docs({_COLUMNS}) VALUES ({', '.join('?' * len(values))}) "
            f"ON CONFLICT(path) DO UPDATE SET {_UPDATE_SET}",
            values,
        )
        rowid = conn.execute(
            "SELECT rowid FROM docs WHERE path = ?", (doc.path,)
        ).fetchone()[0]
        self._clear(conn, doc.path, rowid)
        if broken:
            return
        for key, value in doc.fields.items():
            conn.execute(
                "INSERT INTO fields(path, key, value_json) VALUES (?, ?, ?)",
                (doc.path, key, _json(value)),
            )
        for name, targets in doc.relations.items():
            for target in targets:
                conn.execute(
                    "INSERT INTO relations(src_path, src, rel, dst) VALUES (?, ?, ?, ?)",
                    (doc.path, doc.slug, name, target),
                )
        for tag in doc.tags:
            conn.execute("INSERT INTO tags(path, tag) VALUES (?, ?)", (doc.path, tag))
        conn.execute(
            "INSERT INTO docs_fts(rowid, slug, title, body, path) VALUES (?, ?, ?, ?, ?)",
            (rowid, doc.slug, doc.title or "", doc.body[:FTS_BODY_CAP], doc.path),
        )

    @staticmethod
    def _clear(conn: sqlite3.Connection, path: str, rowid: int) -> None:
        conn.execute("DELETE FROM fields WHERE path = ?", (path,))
        conn.execute("DELETE FROM relations WHERE src_path = ?", (path,))
        conn.execute("DELETE FROM tags WHERE path = ?", (path,))
        conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (rowid,))


def pack_hash(pack: Pack) -> str:
    """SHA-256 of the canonical JSON of every semantic pack field."""
    body = json.dumps(_canonical(asdict(pack)), sort_keys=True, separators=(",", ":"))
    return sha256_hex(body.encode("utf-8"))


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_at(doc: Doc) -> int | None:
    """Whole milliseconds of a record's UTC ``at``; anything else is no activity."""
    if doc.primitive != "record":
        return None
    value = doc.fields.get("at")
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def _inside(root: Path, entry: os.DirEntry[str]) -> bool:
    try:
        return Path(entry.path).resolve().is_relative_to(root)
    except OSError:
        return False


def _connect(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    last: sqlite3.Error | None = None
    for attempt in range(BUSY_RETRIES + 1):
        conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None
        )
        try:
            conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            return conn
        except sqlite3.DatabaseError as exc:
            conn.close()
            if not _is_busy(exc):
                raise
            last = exc
            time.sleep(0.05 * (attempt + 1))
    raise IndexBusy(f"the index at {path} stayed locked") from last


def _matches(conn: sqlite3.Connection, wanted: str) -> bool:
    rows = dict(conn.execute("SELECT key, value FROM meta"))
    return rows.get("schema") == str(INDEX_SCHEMA) and rows.get("pack_hash") == wanted


def _create(conn: sqlite3.Connection, wanted: str) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema', ?)", (str(INDEX_SCHEMA),)
    )
    conn.execute("INSERT INTO meta(key, value) VALUES ('pack_hash', ?)", (wanted,))


def _transaction(
    conn: sqlite3.Connection, work: Callable[[sqlite3.Connection], None]
) -> None:
    last: sqlite3.Error | None = None
    for attempt in range(BUSY_RETRIES + 1):
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc):
                raise
            last = exc
            time.sleep(0.05 * (attempt + 1))
            continue
        try:
            work(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return
    raise IndexBusy("the index stayed locked") from last


def _discard(path: Path) -> None:
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        _unlink(candidate)


def _unlink(path: Path) -> None:
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _is_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    busy = {getattr(sqlite3, "SQLITE_BUSY", -1), getattr(sqlite3, "SQLITE_LOCKED", -1)}
    if code in busy:
        return True
    text = str(exc).lower()
    return "locked" in text or "busy" in text
