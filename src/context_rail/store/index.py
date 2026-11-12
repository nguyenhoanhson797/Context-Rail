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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_rail.core.util import canonical_iso, sha256_hex
from context_rail.model.candidate import CANDIDATE_KIND, parse_candidate
from context_rail.model.cto import parse_check, parse_report, parse_verdict
from context_rail.model.errors import ModelError
from context_rail.model.execution import EXECUTION_KIND, parse_execution
from context_rail.model.fields import (
    integer,
    mapping,
    no_unknown,
    refuse,
    require,
    schema_version,
    string,
    string_list,
)
from context_rail.model.pack import Pack
from context_rail.model.seats import SEAT_GRANTED, SEAT_KINDS, SEAT_REVOKED, parse_seat_event
from context_rail.store.doc import (
    BAD_ENCODING,
    PARSE_ERROR,
    Doc,
    doc_digest,
    parse_doc,
)
from context_rail.store.location import index_path, local_dir
from context_rail.store.lock import file_lock

__all__ = [
    "INDEX_SCHEMA",
    "INVALID_PAYLOAD",
    "TRIAGE_KIND",
    "Index",
    "IndexBusy",
    "SyncStats",
    "pack_hash",
]

INDEX_SCHEMA = 10
#: Reported on the document row when a typed payload cannot be projected.
INVALID_PAYLOAD = "invalid_payload"
#: The Record kind that carries reports into triage.
TRIAGE_KIND = "report.triaged"
EXCERPT_CAP = 500
FTS_BODY_CAP = 20000
BUSY_TIMEOUT_MS = 5000
BUSY_RETRIES = 2
CACHE_LOCK = "index.lock"
#: A cold pass reads every document, so first-access latency is overlapped
#: and each batch is bounded so a store of large bodies stays out of memory.
READ_WORKERS = 16
READ_INLINE = 8
READ_BATCH = 256

_DOC_SUFFIXES = (".md", ".json")
_UNCOUNTED_NAMES = frozenset({"rail.yaml", ".gitignore", ".gitattributes"})
#: Folders whose files are store artifacts, not documents to parse and index.
_UNCOUNTED_DIRS = frozenset({"templates", "packs", "engine-issues"})
_BROKEN_PROBLEMS = frozenset({PARSE_ERROR, BAD_ENCODING})

#: Seek paths the CTO views need; also added to a cache built without them.
_SEEK_INDEXES = (
    "CREATE INDEX IF NOT EXISTS reports_class_at ON reports(target_class, at DESC)",
    "CREATE INDEX IF NOT EXISTS docs_primitive ON docs(primitive, broken, slug, category)",
)

_SCHEMA = (
    """
    CREATE TABLE docs(
        slug TEXT NOT NULL,
        path TEXT PRIMARY KEY,
        primitive TEXT,
        preset TEXT,
        workflow TEXT,
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
    """
    CREATE TABLE checks(
        path TEXT PRIMARY KEY,
        slug TEXT NOT NULL,
        item TEXT,
        mode TEXT NOT NULL,
        acceptance INTEGER NOT NULL,
        definition_etag TEXT NOT NULL,
        inputs_json TEXT NOT NULL,
        spec_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE verdicts(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        "check" TEXT NOT NULL,
        item TEXT,
        at TEXT,
        passed INTEGER NOT NULL,
        check_etag TEXT NOT NULL,
        input_blobs_json TEXT NOT NULL,
        candidate_commit TEXT,
        runner_digest TEXT,
        data_json TEXT NOT NULL
    )
    """,
    'CREATE INDEX verdicts_check_at ON verdicts("check", at DESC)',
    """
    CREATE TABLE code_touch(
        path TEXT NOT NULL,
        symbol TEXT,
        item TEXT NOT NULL,
        record TEXT NOT NULL,
        at TEXT NOT NULL
    )
    """,
    "CREATE INDEX code_touch_path ON code_touch(path, at DESC)",
    "CREATE INDEX code_touch_record ON code_touch(record)",
    """
    CREATE TABLE reports(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        item TEXT,
        run_id TEXT,
        candidate_id TEXT,
        target_class TEXT NOT NULL,
        target TEXT NOT NULL,
        fingerprint TEXT,
        impact TEXT NOT NULL,
        "nothing" INTEGER NOT NULL,
        at TEXT,
        data_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX reports_fingerprint_at ON reports(fingerprint, at)",
    "CREATE INDEX reports_run ON reports(run_id)",
    "CREATE INDEX reports_candidate ON reports(candidate_id)",
    # The digest and the brief read human waits by class on every call, so the
    # scan stays a seek no matter how many reports the store holds.
    *_SEEK_INDEXES,
    """
    CREATE TABLE triaged_reports(
        report_id TEXT NOT NULL,
        triage_id TEXT NOT NULL,
        PRIMARY KEY(report_id, triage_id)
    )
    """,
    """
    CREATE TABLE approvals(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        target TEXT NOT NULL,
        at TEXT,
        protected_hashes_json TEXT NOT NULL,
        confirmer TEXT NOT NULL
    )
    """,
    "CREATE INDEX approvals_target_at ON approvals(target, at DESC)",
    """
    CREATE TABLE reviews(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        item TEXT,
        run_id TEXT,
        candidate_commit TEXT,
        reviewer TEXT,
        verdict TEXT,
        data_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE seats(
        path TEXT PRIMARY KEY,
        at TEXT NOT NULL,
        record_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        seat TEXT NOT NULL,
        role TEXT,
        anchor TEXT,
        profile TEXT,
        actor_by TEXT NOT NULL
    )
    """,
    "CREATE INDEX seats_seat_at ON seats(seat, at)",
    """
    CREATE TABLE executions(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL,
        state TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        outputs_json TEXT NOT NULL,
        error TEXT,
        actor TEXT NOT NULL,
        tool TEXT NOT NULL,
        params_json TEXT NOT NULL,
        runner_digest TEXT,
        at TEXT,
        data_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX executions_target_action ON executions(target, action, attempt DESC)",
    "CREATE INDEX executions_action ON executions(action, state)",
    """
    CREATE TABLE candidates(
        path TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        item TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        base TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        actor TEXT NOT NULL,
        seat TEXT,
        at TEXT NOT NULL,
        data_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX candidates_item_at ON candidates(item, at DESC)",
)

#: Projection tables keyed by the store path of their evidence.
_PATH_TABLES = (
    "checks",
    "verdicts",
    "reports",
    "approvals",
    "reviews",
    "executions",
    "seats",
    "candidates",
)
_PROJECTION_COLUMNS = {
    "checks": (
        "path",
        "slug",
        "item",
        "mode",
        "acceptance",
        "definition_etag",
        "inputs_json",
        "spec_json",
    ),
    "verdicts": (
        "path",
        "record_id",
        '"check"',
        "item",
        "at",
        "passed",
        "check_etag",
        "input_blobs_json",
        "candidate_commit",
        "runner_digest",
        "data_json",
    ),
    "code_touch": ("path", "symbol", "item", "record", "at"),
    "reports": (
        "path",
        "record_id",
        "item",
        "run_id",
        "candidate_id",
        "target_class",
        "target",
        "fingerprint",
        "impact",
        '"nothing"',
        "at",
        "data_json",
    ),
    "triaged_reports": ("report_id", "triage_id"),
    "approvals": (
        "path",
        "record_id",
        "target",
        "at",
        "protected_hashes_json",
        "confirmer",
    ),
    "reviews": (
        "path",
        "record_id",
        "item",
        "run_id",
        "candidate_commit",
        "reviewer",
        "verdict",
        "data_json",
    ),
    "executions": (
        "path",
        "record_id",
        "action",
        "target",
        "state",
        "attempt",
        "started_at",
        "completed_at",
        "outputs_json",
        "error",
        "actor",
        "tool",
        "params_json",
        "runner_digest",
        "at",
        "data_json",
    ),
    "seats": (
        "path",
        "at",
        "record_id",
        "kind",
        "seat",
        "role",
        "anchor",
        "profile",
        "actor_by",
    ),
    "candidates": (
        "path",
        "record_id",
        "item",
        "commit_sha",
        "base",
        "attempt",
        "actor",
        "seat",
        "at",
        "data_json",
    ),
}

_VERDICT_KIND = "check.verdict"
_REPORT_KIND = "report.filed"
_APPROVAL_KIND = "approval.recorded"
_REVIEW_KIND = "review.verdict"
_PROJECTED_KINDS = frozenset(
    {
        _VERDICT_KIND,
        _REPORT_KIND,
        _APPROVAL_KIND,
        _REVIEW_KIND,
        CANDIDATE_KIND,
        EXECUTION_KIND,
        TRIAGE_KIND,
        SEAT_GRANTED,
        SEAT_REVOKED,
    }
)
_TRIAGE_KEYS = ("schema", "reports", "cluster", "weight", "action", "outputs")
_TRIAGE_ACTIONS = ("deferred", "acknowledged", "fix", "proposal", "export")
_APPROVAL_KEYS = (
    "schema",
    "target",
    "protected_hashes",
    "confirmer",
    "confirmation",
    "scope",
    "files",
    "snapshot",
    "diff",
    "diff_omitted",
)
_APPROVAL_REQUIRED = ("target", "protected_hashes", "confirmer", "confirmation")
_REVIEW_KEYS = (
    "schema",
    "item",
    "implementation_run",
    "implementer_actor",
    "reviewer_actor",
    "implementer_model",
    "reviewer_model",
    "candidate_commit",
    "verdict",
    "findings",
    "evidence",
    "profile_version",
    "candidate_record",
    "notes",
    "harness",
)
_REVIEW_REQUIRED = ("schema", "item", "reviewer_actor", "candidate_commit", "verdict")
_REVIEW_VERDICTS = ("approve", "reject")


_COLUMNS = (
    "slug, path, primitive, preset, workflow, title, status, category, etag, "
    "mtime_ns, size, created, updated, excerpt, problems, digest, record_at, broken"
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
                _ensure_seek_indexes(conn)
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
            for table in ("docs", "fields", "relations", "tags", "docs_fts", *_PROJECTION_COLUMNS):
                conn.execute(f"DELETE FROM {table}")

        with file_lock(local_dir(self.store) / CACHE_LOCK):
            _transaction(self.conn, clear)
        return self.sync()

    def close(self) -> None:
        """Close the connection, releasing the database file."""
        self.conn.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

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
                if _is_linked_dir(entry):
                    continue
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

    def _payloads(self, paths: list[str]) -> dict[str, bytes]:
        """Read the bytes of one batch of re-parsed documents, overlapping access.

        A cold pass opens every file once; that latency is I/O-bound, so a
        thread pool overlaps it while the parse and SQL work stays serial.
        """
        if len(paths) <= READ_INLINE:
            return {path: self._read(path) for path in paths}
        with ThreadPoolExecutor(max_workers=min(READ_WORKERS, len(paths))) as pool:
            return dict(zip(paths, pool.map(self._read, paths)))

    def _read(self, path: str) -> bytes:
        return (self.store / path).read_bytes()

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
        targets = [*added, *changed]
        for start in range(0, len(targets), READ_BATCH):
            chunk = targets[start : start + READ_BATCH]
            payloads = self._payloads(chunk)
            # BEGIN IMMEDIATE holds the write lock, so this read cannot race a
            # concurrent sync that already stored one of these paths.
            known = dict(
                conn.execute(
                    f"SELECT path, rowid FROM docs WHERE path IN "
                    f"({', '.join('?' * len(chunk))})",
                    chunk,
                )
            )
            for path in chunk:
                mtime_ns, size = seen[path]
                data = payloads[path]
                doc = parse_doc(path, data)
                self._store(conn, doc, data, mtime_ns, size, rowid=known.get(path))

    def _store(
        self,
        conn: sqlite3.Connection,
        doc: Doc,
        data: bytes,
        mtime_ns: int,
        size: int,
        *,
        rowid: int | None = None,
    ) -> None:
        broken = bool(_BROKEN_PROBLEMS.intersection(doc.problems))
        category = self.pack.statuses.get(doc.status) if doc.status else None
        declared = self.pack.presets.get(doc.preset or "")
        workflow = declared.workflow if declared is not None else "default"
        record_at = _record_at(doc)
        projected: list[tuple[str, tuple[Any, ...]]] = []
        problems = list(doc.problems)
        if not broken:
            try:
                projected = _projection(doc)
            except ModelError:
                problems.append(INVALID_PAYLOAD)
        values = (
            doc.slug,
            doc.path,
            doc.primitive,
            doc.preset,
            workflow,
            doc.title,
            doc.status,
            category,
            doc.etag,
            mtime_ns,
            size,
            doc.created,
            doc.updated,
            doc.body[:EXCERPT_CAP],
            _json(problems),
            doc_digest(doc.path, data),
            record_at,
            1 if broken else 0,
        )
        cursor = conn.execute(
            f"INSERT INTO docs({_COLUMNS}) VALUES ({', '.join('?' * len(values))}) "
            f"ON CONFLICT(path) DO UPDATE SET {_UPDATE_SET}",
            values,
        )
        if rowid is None:
            rowid = cursor.lastrowid
        else:
            self._clear(conn, doc.path, rowid)
        if broken:
            return
        if doc.fields:
            conn.executemany(
                "INSERT INTO fields(path, key, value_json) VALUES (?, ?, ?)",
                [(doc.path, key, _json(value)) for key, value in doc.fields.items()],
            )
        rows = [
            (doc.path, doc.slug, name, target)
            for name, targets in doc.relations.items()
            for target in targets
        ]
        if rows:
            conn.executemany(
                "INSERT INTO relations(src_path, src, rel, dst) VALUES (?, ?, ?, ?)",
                rows,
            )
        if doc.tags:
            conn.executemany(
                "INSERT INTO tags(path, tag) VALUES (?, ?)",
                [(doc.path, tag) for tag in doc.tags],
            )
        conn.execute(
            "INSERT INTO docs_fts(rowid, slug, title, body, path) VALUES (?, ?, ?, ?, ?)",
            (rowid, doc.slug, doc.title or "", doc.body[:FTS_BODY_CAP], doc.path),
        )
        self._write_projection(conn, projected)

    def _write_projection(
        self, conn: sqlite3.Connection, rows: list[tuple[str, tuple[Any, ...]]]
    ) -> None:
        for table, values in rows:
            columns = _PROJECTION_COLUMNS[table]
            conn.execute(
                f"INSERT OR REPLACE INTO {table}({', '.join(columns)}) "
                f"VALUES ({', '.join('?' * len(values))})",
                values,
            )

    @staticmethod
    def _clear(conn: sqlite3.Connection, path: str, rowid: int) -> None:
        """Drop every row one document contributed."""
        candidate = conn.execute(
            "SELECT record_id FROM candidates WHERE path = ?", (path,)
        ).fetchone()
        if candidate is not None:
            conn.execute("DELETE FROM code_touch WHERE record = ?", (candidate[0],))
        report = conn.execute(
            "SELECT record_id FROM reports WHERE path = ?", (path,)
        ).fetchone()
        if report is not None:
            conn.execute("DELETE FROM triaged_reports WHERE report_id = ?", (report[0],))
        slug = conn.execute("SELECT slug FROM docs WHERE path = ?", (path,)).fetchone()
        if slug is not None:
            conn.execute("DELETE FROM triaged_reports WHERE triage_id = ?", (slug[0],))
        for table in _PATH_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
        conn.execute("DELETE FROM fields WHERE path = ?", (path,))
        conn.execute("DELETE FROM relations WHERE src_path = ?", (path,))
        conn.execute("DELETE FROM tags WHERE path = ?", (path,))
        conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (rowid,))


def pack_hash(pack: Pack) -> str:
    """SHA-256 of the canonical JSON of every semantic pack field.

    A v1 pack has no operating block, so the key is omitted rather than
    written as null: its digest stays exactly what it was before v5.
    """
    from context_rail.model.compiler import canonical_json, semantic_payload

    return sha256_hex(canonical_json(semantic_payload(pack)).encode("utf-8"))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _projection(doc: Doc) -> list[tuple[str, tuple[Any, ...]]]:
    """The typed rows one document contributes, or an empty list for no shape."""
    if doc.primitive == "check":
        return [("checks", _check_row(doc))]
    if doc.primitive != "record":
        return []
    kind = doc.fields.get("kind")
    if kind is None:
        return []
    if not isinstance(kind, str):
        raise ModelError("VALIDATION_FAILED", "record kind must be text")
    if kind not in _PROJECTED_KINDS:
        return []
    data = doc.fields.get("data")
    if not isinstance(data, dict):
        raise ModelError("VALIDATION_FAILED", "record data must be a mapping")
    record_id = _record_id(doc)
    if kind == _VERDICT_KIND:
        return _verdict_rows(doc, record_id, data)
    if kind == _REPORT_KIND:
        return _report_rows(doc, record_id, data)
    if kind == TRIAGE_KIND:
        return _triage_rows(record_id, data)
    if kind == _APPROVAL_KIND:
        return _approval_rows(doc, record_id, data)
    if kind == EXECUTION_KIND:
        return _execution_rows(doc, record_id, data)
    if kind == CANDIDATE_KIND:
        return _candidate_rows(doc, record_id, data)
    if kind in SEAT_KINDS:
        return _seat_rows(doc, record_id, kind, data)
    return _review_rows(doc, record_id, data)


def _check_row(doc: Doc) -> tuple[Any, ...]:
    fields = dict(doc.fields)
    if doc.relations.get("verifies"):
        fields["verifies"] = list(doc.relations["verifies"])
    spec = parse_check(fields)
    item = spec.verifies[0] if spec.verifies else None
    return (
        doc.path,
        doc.slug,
        item,
        spec.mode,
        1 if spec.acceptance else 0,
        doc.etag,
        _json(list(spec.inputs)),
        _json(fields),
    )


def _verdict_rows(doc: Doc, record_id: str, data: dict[str, Any]) -> list[tuple[str, tuple[Any, ...]]]:
    verdict = parse_verdict(data)
    about = tuple(doc.relations.get("about", ()))
    if about and about[0] == verdict.check:
        item = about[1] if len(about) > 1 else None
    else:
        item = next((target for target in about if target != verdict.check), None)
    return [
        (
            "verdicts",
            (
                doc.path,
                record_id,
                verdict.check,
                item,
                _at(doc),
                1 if verdict.passed else 0,
                verdict.check_etag,
                _json(dict(verdict.input_blobs)),
                verdict.candidate_commit,
                verdict.runner_digest,
                _json(data),
            ),
        )
    ]


def _report_rows(doc: Doc, record_id: str, data: dict[str, Any]) -> list[tuple[str, tuple[Any, ...]]]:
    report = parse_report(data)
    return [
        (
            "reports",
            (
                doc.path,
                record_id,
                report.item,
                report.run,
                report.candidate,
                report.target_class,
                report.target,
                report.fingerprint,
                report.impact,
                1 if report.nothing_to_report else 0,
                _at(doc),
                _json(data),
            ),
        )
    ]


def _triage_rows(record_id: str, data: dict[str, Any]) -> list[tuple[str, tuple[Any, ...]]]:
    payload = _strict(
        data, _TRIAGE_KEYS, "triage", ("reports", "cluster", "weight", "action")
    )
    reports = string_list(payload["reports"], "triage.reports")
    string(payload["cluster"], "triage.cluster")
    integer(payload["weight"], "triage.weight", positive=True)
    action = string(payload["action"], "triage.action")
    if action not in _TRIAGE_ACTIONS:
        refuse("triage.action must be one of " + ", ".join(_TRIAGE_ACTIONS))
    string_list(payload.get("outputs", []), "triage.outputs", allow_empty=True)
    return [("triaged_reports", (report_id, record_id)) for report_id in reports]


def _approval_rows(doc: Doc, record_id: str, data: dict[str, Any]) -> list[tuple[str, tuple[Any, ...]]]:
    payload = _strict(data, _APPROVAL_KEYS, "approval", _APPROVAL_REQUIRED)
    target = string(payload["target"], "approval.target")
    hashes = mapping(payload["protected_hashes"], "approval.protected_hashes")
    for path, digest in hashes.items():
        string(path, "approval.protected_hashes path")
        string(digest, "approval.protected_hashes digest")
    confirmer = string(payload["confirmer"], "approval.confirmer")
    string(payload["confirmation"], "approval.confirmation")
    row = (doc.path, record_id, target, _at(doc), _json(hashes), confirmer)
    return [("approvals", row)]


def _review_rows(doc: Doc, record_id: str, data: dict[str, Any]) -> list[tuple[str, tuple[Any, ...]]]:
    payload = _strict(data, _REVIEW_KEYS, "review", _REVIEW_REQUIRED)
    item = string(payload["item"], "review.item")
    run_id = _optional_text(payload.get("implementation_run"), "review.implementation_run")
    for name in (
        "implementer_actor",
        "implementer_model",
        "reviewer_model",
        "candidate_record",
        "notes",
        "harness",
    ):
        _optional_text(payload.get(name), f"review.{name}")
    reviewer = string(payload["reviewer_actor"], "review.reviewer_actor")
    candidate = string(payload["candidate_commit"], "review.candidate_commit")
    verdict = string(payload["verdict"], "review.verdict")
    if verdict not in _REVIEW_VERDICTS:
        refuse("review.verdict must be approve or reject")
    for name in ("findings", "evidence"):
        if payload.get(name) is not None:
            string_list(payload[name], f"review.{name}", allow_empty=True)
    if payload.get("profile_version") is not None:
        integer(payload["profile_version"], "review.profile_version", positive=True)
    row = (doc.path, record_id, item, run_id, candidate, reviewer, verdict, _json(data))
    return [("reviews", row)]


def _seat_rows(
    doc: Doc, record_id: str, kind: str, data: dict[str, Any]
) -> list[tuple[str, tuple[Any, ...]]]:
    at = _at(doc)
    if at is None:
        refuse("seat record needs an ISO 8601 at")
    event = parse_seat_event(kind, data, at=at, record_id=record_id)
    row = (
        doc.path,
        at,
        record_id,
        kind,
        event.seat,
        event.role,
        event.anchor,
        event.profile,
        event.by,
    )
    return [("seats", row)]


def _execution_rows(
    doc: Doc, record_id: str, data: dict[str, Any]
) -> list[tuple[str, tuple[Any, ...]]]:
    execution = parse_execution(data)
    row = (
        doc.path,
        record_id,
        execution.action,
        execution.target,
        execution.state,
        execution.attempt,
        execution.started_at,
        execution.completed_at,
        _json(list(execution.outputs)),
        execution.error,
        execution.actor,
        execution.tool,
        _json(execution.params),
        execution.runner_digest,
        _at(doc),
        _json(data),
    )
    return [("executions", row)]


def _candidate_rows(
    doc: Doc, record_id: str, data: dict[str, Any]
) -> list[tuple[str, tuple[Any, ...]]]:
    candidate = parse_candidate(data)
    rows: list[tuple[str, tuple[Any, ...]]] = [
        (
            "candidates",
            (
                doc.path,
                record_id,
                candidate.item,
                candidate.commit,
                candidate.base,
                candidate.attempt,
                candidate.actor,
                candidate.seat,
                candidate.at,
                _json(data),
            ),
        )
    ]
    rows.extend(
        ("code_touch", (path, None, candidate.item, record_id, candidate.at))
        for path in candidate.files_changed
    )
    return rows


def _strict(
    data: dict[str, Any], allowed: tuple[str, ...], what: str, required: tuple[str, ...]
) -> dict[str, Any]:
    payload = mapping(data, what)
    schema_version(payload, 1, what)
    no_unknown(payload, allowed, what)
    require(payload, required, what)
    return payload


def _optional_text(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    return string(raw, what)


def _timestamp(raw: Any, what: str) -> str:
    value = canonical_iso(string(raw, what))
    if value is None:
        refuse(f"{what} must be an ISO 8601 timestamp")
    return value


def _at(doc: Doc, payload: Mapping[str, Any] | None = None) -> str | None:
    for raw in (doc.fields.get("at"), (payload or {}).get("at")):
        if isinstance(raw, str):
            value = canonical_iso(raw)
            if value is not None:
                return value
    return None


def _record_id(doc: Doc) -> str:
    value = doc.fields.get("id")
    return value if isinstance(value, str) and value else doc.slug


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


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _inside(root: Path, entry: os.DirEntry[str]) -> bool:
    try:
        return Path(entry.path).resolve().is_relative_to(root)
    except OSError:
        return False


def _is_linked_dir(entry: os.DirEntry[str]) -> bool:
    """True when a directory entry is a symlink, junction or other reparse point.

    ``DirEntry.is_symlink`` misses Windows junctions and ``Path.is_junction``
    only exists from Python 3.12, so the reparse-point file attribute decides.
    """
    if not entry.is_dir():
        return False
    if entry.is_symlink():
        return True
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


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
    if rows.get("schema") != str(INDEX_SCHEMA) or rows.get("pack_hash") != wanted:
        return False
    return conn.execute("PRAGMA user_version").fetchone()[0] == INDEX_SCHEMA


def _ensure_seek_indexes(conn: sqlite3.Connection) -> None:
    """Add the query indexes a cache built before them is still missing.

    They are additive and keyed on existing tables, so an older cache keeps its
    rows and gains the seek paths the CTO views need at ten thousand documents.
    """
    for statement in _SEEK_INDEXES:
        conn.execute(statement)
    conn.commit()


def _create(conn: sqlite3.Connection, wanted: str) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema', ?)", (str(INDEX_SCHEMA),)
    )
    conn.execute("INSERT INTO meta(key, value) VALUES ('pack_hash', ?)", (wanted,))
    conn.execute(f"PRAGMA user_version = {INDEX_SCHEMA}")


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
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        try:
            conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
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
