"""Bounded reads over the index: find, show, records and activity.

Every query here runs as SQL over the index. Only ``show`` opens a store file,
and only the requested document; nothing in this module launches a process,
hashes a source tree or parses documents it was not asked for.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from context_rail.model.primitives import CATEGORIES, PRIMITIVES, RELATIONS
from context_rail.store.doc import parse_doc
from context_rail.store.index import Index

__all__ = ["QueryError", "find", "last_activity_ms", "records_about", "show"]

DEFAULT_LIMIT = 50
LIMIT_BOUNDS = (1, 100)
DEFAULT_BODY_CHARS = 4000
BODY_CHARS_BOUNDS = (0, 20000)
DEFAULT_RECORD_LIMIT = 20

_SQLITE_ERROR = getattr(sqlite3, "SQLITE_ERROR", 1)

_ITEM_COLUMNS = "d.slug, d.title, d.primitive, d.preset, d.status, d.path"

_DOC_COLUMNS = (
    "slug, path, primitive, preset, title, status, category, created, updated, "
    "etag, digest, problems"
)


class QueryError(Exception):
    """A query refused before it runs.

    ``code`` matches the model layer's refusal code so the gateway can map the
    exception onto a Result without reading the message.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "VALIDATION_FAILED"
        self.message = message


def find(
    ix: Index,
    *,
    primitive: str | None = None,
    preset: str | None = None,
    tags: tuple[str, ...] = (),
    status: str | None = None,
    category: str | None = None,
    text: str | None = None,
    rel: tuple[str, str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Page through documents matching every supplied filter.

    Results are ordered by ``updated`` descending, then slug, then path. The
    total counts each document once no matter how many of its rows match, and
    ``omitted`` reports what the page left behind.
    """
    limit = _bounded(limit, LIMIT_BOUNDS, "limit")
    primitive = _optional_name(primitive, "primitive", PRIMITIVES)
    category = _optional_name(category, "category", CATEGORIES)
    preset = _optional_name(preset, "preset")
    status = _optional_name(status, "status")
    wanted_tags = _tag_list(tags)
    relation = _relation_filter(rel)
    term = _text_filter(text)

    where = ["d.broken = 0"]
    params: list[Any] = []
    for column, value in (
        ("primitive", primitive),
        ("preset", preset),
        ("status", status),
        ("category", category),
    ):
        if value is not None:
            where.append(f"d.{column} = ?")
            params.append(value)
    for tag in wanted_tags:
        where.append(
            "EXISTS (SELECT 1 FROM tags t WHERE t.path = d.path AND t.tag = ?)"
        )
        params.append(tag)
    if relation is not None:
        where.append(
            "EXISTS (SELECT 1 FROM relations r "
            "WHERE r.src_path = d.path AND r.rel = ? AND r.dst = ?)"
        )
        params.extend(relation)
    if term is not None:
        where.append("d.rowid IN (SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?)")
        params.append(term)
    clause = " AND ".join(where)

    try:
        total = ix.conn.execute(
            f"SELECT COUNT(DISTINCT d.path) FROM docs d WHERE {clause}", params
        ).fetchone()[0]
        rows = ix.conn.execute(
            f"SELECT {_ITEM_COLUMNS} FROM docs d WHERE {clause} "
            "ORDER BY d.updated DESC, d.slug ASC, d.path ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if term is not None and getattr(exc, "sqlite_errorcode", None) == _SQLITE_ERROR:
            raise QueryError(f"malformed text query {term!r}: {exc}") from exc
        raise

    items = [
        {
            "slug": slug,
            "title": title,
            "primitive": kind,
            "preset": preset_name,
            "status": state,
            "path": path,
        }
        for slug, title, kind, preset_name, state, path in rows
    ]
    return {"items": items, "total": total, "omitted": total - len(items)}


def show(ix: Index, slug: str, *, body_chars: int = DEFAULT_BODY_CHARS) -> dict:
    """One document with its relations and a bounded body.

    Raises ``LookupError("not_found")`` when no document has the slug, and
    ``LookupError("ambiguous")`` when more than one does; the gateway turns the
    latter into a CONFLICT naming the candidate paths.
    """
    body_chars = _bounded(body_chars, BODY_CHARS_BOUNDS, "body_chars")
    slug = _slug(slug)

    paths = [
        path
        for (path,) in ix.conn.execute(
            "SELECT path FROM docs WHERE slug = ? ORDER BY path", (slug,)
        )
    ]
    if not paths:
        raise LookupError("not_found")
    if len(paths) > 1:
        raise LookupError("ambiguous")
    path = paths[0]

    row = ix.conn.execute(
        f"SELECT {_DOC_COLUMNS} FROM docs WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        raise LookupError("not_found")
    (
        found_slug,
        found_path,
        primitive,
        preset,
        title,
        status,
        category,
        created,
        updated,
        etag,
        digest,
        problems,
    ) = row

    tags = [
        tag
        for (tag,) in ix.conn.execute(
            "SELECT tag FROM tags WHERE path = ? ORDER BY rowid", (path,)
        )
    ]
    fields = {
        key: json.loads(value)
        for key, value in ix.conn.execute(
            "SELECT key, value_json FROM fields WHERE path = ? ORDER BY rowid", (path,)
        )
    }
    out = _grouped(
        ix.conn.execute(
            "SELECT rel, dst FROM relations WHERE src_path = ? ORDER BY rel, dst",
            (path,),
        )
    )
    incoming = _grouped(
        ix.conn.execute(
            "SELECT rel, src FROM relations WHERE dst = ? ORDER BY rel, src", (slug,)
        )
    )

    body = parse_doc(path, (ix.store / path).read_bytes()).body
    visible = body[:body_chars]
    return {
        "doc": {
            "slug": found_slug,
            "path": found_path,
            "primitive": primitive,
            "preset": preset,
            "title": title,
            "status": status,
            "category": category,
            "tags": tags,
            "created": created,
            "updated": updated,
            "etag": etag,
            "digest": digest,
            "problems": json.loads(problems),
            "fields": fields,
        },
        "out": out,
        "in": incoming,
        "body": visible,
        "body_omitted": len(body) - len(visible),
    }


def records_about(
    ix: Index, slug: str, limit: int = DEFAULT_RECORD_LIMIT
) -> list[dict[str, Any]]:
    """Records naming ``slug`` under ``about``, newest record first.

    Ties on ``at`` fall back to the record id descending, then path, so the
    order is deterministic. Payloads are rebuilt from indexed metadata only.
    """
    slug = _slug(slug)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise QueryError("limit must be a non-negative integer")

    rows = ix.conn.execute(
        """
        SELECT d.path, d.slug, d.primitive, d.preset, d.digest
        FROM docs d
        JOIN relations r ON r.src_path = d.path
        WHERE r.rel = 'about' AND r.dst = ?
          AND d.primitive = 'record' AND d.broken = 0
        GROUP BY d.path
        ORDER BY d.record_at DESC, d.slug DESC, d.path ASC
        LIMIT ?
        """,
        (slug, limit),
    ).fetchall()

    payloads: list[dict[str, Any]] = []
    for path, record_slug, primitive, preset, digest in rows:
        fields = {
            key: json.loads(value)
            for key, value in ix.conn.execute(
                "SELECT key, value_json FROM fields WHERE path = ? ORDER BY rowid",
                (path,),
            )
        }
        about = [
            target
            for (target,) in ix.conn.execute(
                "SELECT DISTINCT dst FROM relations "
                "WHERE src_path = ? AND rel = 'about' ORDER BY dst",
                (path,),
            )
        ]
        payloads.append(
            {
                "id": fields.get("id") or record_slug,
                "primitive": primitive,
                "preset": preset,
                "kind": fields.get("kind"),
                "at": fields.get("at"),
                "actor": fields.get("actor"),
                "about": about,
                "data": fields.get("data", {}),
                "digest": digest,
            }
        )
    return payloads


def last_activity_ms(ix: Index) -> dict[str, int]:
    """The newest valid record ``at`` about each existing slug, in epoch ms."""
    rows = ix.conn.execute(
        """
        SELECT d.slug, MAX(rec.record_at)
        FROM docs d
        JOIN relations r ON r.dst = d.slug AND r.rel = 'about'
        JOIN docs rec ON rec.path = r.src_path
        WHERE rec.primitive = 'record' AND rec.record_at IS NOT NULL
          AND d.broken = 0
        GROUP BY d.slug
        """
    ).fetchall()
    return {slug: value for slug, value in rows if value is not None}


def _bounded(value: object, bounds: tuple[int, int], name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{name} must be an integer, not {type(value).__name__}")
    low, high = bounds
    if not low <= value <= high:
        raise QueryError(f"{name} must be between {low} and {high}, not {value}")
    return value


def _optional_name(
    value: object, name: str, allowed: tuple[str, ...] | None = None
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise QueryError(f"{name} must be a non-empty string")
    if allowed is not None and value not in allowed:
        raise QueryError(f"unknown {name} {value!r}")
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise QueryError("slug must be a non-empty string")
    return value


def _tag_list(tags: object) -> tuple[str, ...]:
    if tags is None:
        return ()
    if isinstance(tags, str):
        raise QueryError("tags must be a sequence of strings, not one string")
    try:
        items = tuple(tags)  # type: ignore[arg-type]
    except TypeError:
        raise QueryError("tags must be a sequence of strings") from None
    for item in items:
        if not isinstance(item, str) or not item:
            raise QueryError("every tag must be a non-empty string")
    return items


def _relation_filter(rel: object) -> tuple[str, str] | None:
    if rel is None:
        return None
    if isinstance(rel, str) or not isinstance(rel, (tuple, list)) or len(rel) != 2:
        raise QueryError("rel must be a (relation, slug) pair")
    name, target = rel
    if name not in RELATIONS:
        raise QueryError(f"unknown relation {name!r}")
    if not isinstance(target, str) or not target:
        raise QueryError("relation target must be a non-empty string")
    return name, target


def _text_filter(text: object) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise QueryError("text must be a string")
    term = text.strip()
    return term or None


def _grouped(rows: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {}
    for name, value in rows:
        grouped.setdefault(name, set()).add(value)
    return {name: sorted(values) for name, values in grouped.items()}
