"""Bounded reads over the index: find, show, records and activity.

Every query here runs as SQL over the index. Only ``show`` opens a store file,
and only the requested document; nothing in this module launches a process,
hashes a source tree or parses documents it was not asked for.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from context_rail.core.util import canonical_iso
from context_rail.model.cto import USAGE_KEYS
from context_rail.model.primitives import CATEGORIES, PRIMITIVES, RELATIONS
from context_rail.store.doc import parse_doc
from context_rail.store.index import Index

__all__ = [
    "QueryError",
    "approval_for",
    "brief_rows",
    "check_state",
    "check_states",
    "digest_rows",
    "find",
    "last_activity_ms",
    "ledger",
    "records_about",
    "review_for",
    "review_for_item",
    "show",
    "untriaged_reports",
]

DEFAULT_LIMIT = 50
LIMIT_BOUNDS = (1, 100)
DEFAULT_BODY_CHARS = 4000
BODY_CHARS_BOUNDS = (0, 20000)
DEFAULT_RECORD_LIMIT = 20
LEDGER_LIMIT_BOUNDS = (1, 100)
REPORT_LIMIT_BOUNDS = (1, 1000)
FEED_LIMIT_BOUNDS = (1, 100)

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


_CHECK_COLUMNS = (
    "c.slug, c.item, c.mode, c.acceptance, c.definition_etag, c.inputs_json, c.spec_json"
)


def check_states(ix: Index, item: str | None = None) -> list[dict[str, Any]]:
    """Every Check declared for one Item (or all Checks), with its newest proof.

    The returned freshness inputs are what a caller compares against the Git
    index; this helper never reads a source file or runs a command.
    """
    if item is None:
        rows = ix.conn.execute(
            f"SELECT {_CHECK_COLUMNS} FROM checks c ORDER BY c.slug, c.path"
        ).fetchall()
    else:
        rows = ix.conn.execute(
            f"SELECT {_CHECK_COLUMNS} FROM checks c WHERE c.item = ? ORDER BY c.slug, c.path",
            (_slug(item),),
        ).fetchall()
    return [_check_state(ix, row) for row in rows]


def check_state(ix: Index, slug: str) -> dict[str, Any] | None:
    """One Check with its newest proof, or None when no Check carries ``slug``."""
    row = ix.conn.execute(
        f"SELECT {_CHECK_COLUMNS} FROM checks c WHERE c.slug = ? ORDER BY c.path LIMIT 1",
        (_slug(slug),),
    ).fetchone()
    return None if row is None else _check_state(ix, row)


def _check_state(ix: Index, row: Sequence[Any]) -> dict[str, Any]:
    slug, owner, mode, acceptance, definition_etag, inputs_json, spec_json = row
    latest = ix.conn.execute(
        "SELECT record_id, at, passed, check_etag, input_blobs_json, "
        "candidate_commit, runner_digest, data_json FROM verdicts "
        'WHERE "check" = ? ORDER BY at DESC, record_id DESC LIMIT 1',
        (slug,),
    ).fetchone()
    state: dict[str, Any] = {
        "slug": slug,
        "item": owner,
        "mode": mode,
        "acceptance": bool(acceptance),
        "definition_etag": definition_etag,
        "inputs": json.loads(inputs_json),
        "spec": json.loads(spec_json),
        "passed": None,
        "verdict": None,
        "at": None,
        "check_etag": None,
        "input_blobs": {},
        "input_globs": [],
        "candidate_commit": None,
        "runner_digest": None,
        "reason": None,
    }
    if latest is not None:
        record_id, at, passed, check_etag, blobs, candidate, digest, data = latest
        payload = json.loads(data)
        state.update(
            {
                "passed": bool(passed),
                "verdict": record_id,
                "at": at,
                "check_etag": check_etag,
                "input_blobs": json.loads(blobs),
                "input_globs": payload.get("input_globs", []),
                "candidate_commit": candidate,
                "runner_digest": digest,
                "reason": payload.get("reason"),
            }
        )
    return state


def ledger(
    ix: Index,
    *,
    item: str | None = None,
    profile: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Runs with their attempt-counted usage, bounded by ``limit``.

    Totals cover every matching run, not just the page. Attempt rows are read
    once per run, so a run with a report, verdict and review is still counted
    once, and a counter that was never reported stays out of ``known_sum``.
    """
    limit = _bounded(limit, LEDGER_LIMIT_BOUNDS, "limit")
    item = _optional_name(item, "item")
    profile = _optional_name(profile, "profile")
    since = _since(since)
    clause, params = _run_clause(item, profile, since)

    total = ix.conn.execute(
        f"SELECT COUNT(*) FROM runs r WHERE {clause}", params
    ).fetchone()[0]
    rows = ix.conn.execute(
        f"""
        SELECT r.record_id, r.dispatch_id, r.role, r.item, r.profile, r.model,
               r."start", r."end", r.duration_s, r.status, r.attempt_count,
               r.candidate_commit, r.accepted, r.data_json,
               (SELECT p.record_id FROM reports p WHERE p.run_id = r.record_id
                ORDER BY p.at DESC, p.record_id DESC LIMIT 1)
        FROM runs r WHERE {clause}
        ORDER BY r."end" DESC, r.record_id DESC LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    usage = _usage_for_runs(ix, [row[0] for row in rows])
    runs: list[dict[str, Any]] = []
    for row in rows:
        (
            record_id,
            dispatch_id,
            role,
            item_name,
            profile_name,
            model,
            start,
            end,
            duration_s,
            status,
            attempt_count,
            candidate,
            accepted,
            data_json,
            report,
        ) = row
        report = report or json.loads(data_json).get("report")
        runs.append(
            {
                "run": record_id,
                "record_id": record_id,
                "dispatch_id": dispatch_id,
                "role": role,
                "item": item_name,
                "profile": profile_name,
                "model": model,
                "start": start,
                "end": end,
                "duration_s": duration_s,
                "status": status,
                "attempt_count": attempt_count,
                "candidate_commit": candidate,
                "accepted": None if accepted is None else bool(accepted),
                "usage": _visible_usage(usage.get(record_id, ())),
                "report": report,
            }
        )
    totals = _visible_usage(_attempt_usages(ix, clause, params))
    return {
        "runs": runs,
        "totals": totals,
        "unknown_counts": totals["unknown_attempts"],
        "total": total,
        "omitted": total - len(runs),
    }


def untriaged_reports(ix: Index, limit: int = 100) -> list[dict[str, Any]]:
    """Reports no triage Record has claimed yet, newest first."""
    limit = _bounded(limit, REPORT_LIMIT_BOUNDS, "limit")
    rows = ix.conn.execute(
        """
        SELECT p.record_id, p.item, p.run_id, p.target_class, p.target,
               p.fingerprint, p.impact, p."nothing", p.at, p.data_json, p.path
        FROM reports p
        WHERE NOT EXISTS (
            SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id
        )
        ORDER BY p.at DESC, p.record_id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "report": record_id,
            "record_id": record_id,
            "item": item,
            "run": run_id,
            "target_class": target_class,
            "target": target,
            "fingerprint": fingerprint,
            "impact": impact,
            "nothing": bool(nothing),
            "at": at,
            "data": json.loads(data),
            "path": path,
        }
        for (
            record_id,
            item,
            run_id,
            target_class,
            target,
            fingerprint,
            impact,
            nothing,
            at,
            data,
            path,
        ) in rows
    ]


def review_for(ix: Index, candidate: str) -> dict[str, Any] | None:
    """The newest review bound to exactly ``candidate``, or None."""
    candidate = _text(candidate, "candidate")
    row = ix.conn.execute(
        """
        SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.reviewer,
               r.verdict, r.data_json, r.path
        FROM reviews r JOIN docs d ON d.path = r.path
        WHERE r.candidate_commit = ?
        ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1
        """,
        (candidate,),
    ).fetchone()
    if row is None:
        return None
    record_id, item, run_id, bound, reviewer, verdict, data_json, path = row
    payload = json.loads(data_json)
    return {
        "review": record_id,
        "record_id": record_id,
        "item": item,
        "run": run_id,
        "candidate_commit": bound,
        "reviewer": reviewer,
        "verdict": verdict,
        "findings": payload.get("findings", []),
        "evidence": payload.get("evidence", []),
        "data": payload,
        "path": path,
    }


def review_for_item(ix: Index, item: str) -> dict[str, Any] | None:
    """The newest review of one Item, or None when nothing reviewed it."""
    item = _text(item, "item")
    row = ix.conn.execute(
        """
        SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.reviewer,
               r.verdict, r.data_json, r.path
        FROM reviews r JOIN docs d ON d.path = r.path
        WHERE r.item = ?
        ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1
        """,
        (item,),
    ).fetchone()
    if row is None:
        return None
    record_id, owner, run_id, bound, reviewer, verdict, data_json, path = row
    payload = json.loads(data_json)
    return {
        "review": record_id,
        "record_id": record_id,
        "item": owner,
        "run": run_id,
        "candidate_commit": bound,
        "reviewer": reviewer,
        "verdict": verdict,
        "findings": payload.get("findings", []),
        "evidence": payload.get("evidence", []),
        "data": payload,
        "path": path,
    }


def approval_for(ix: Index, target: str) -> dict[str, Any] | None:
    """The newest approval for ``target``, by ``(at, id)``; never an older one."""
    target = _text(target, "target")
    row = ix.conn.execute(
        "SELECT record_id, target, at, protected_hashes_json, confirmer, path "
        "FROM approvals WHERE target = ? "
        "ORDER BY at DESC, record_id DESC LIMIT 1",
        (target,),
    ).fetchone()
    if row is None:
        return None
    record_id, found_target, at, hashes_json, confirmer, path = row
    payload = _record_data(ix, path)
    return {
        "approval": record_id,
        "record_id": record_id,
        "target": found_target,
        "at": at,
        "protected_hashes": json.loads(hashes_json),
        "confirmer": confirmer,
        "confirmation": payload.get("confirmation"),
        "data": payload,
        "path": path,
    }


def brief_rows(ix: Index, limit: int = 20) -> dict[str, Any]:
    """Complete counts plus bounded feeds for the CTO brief sections."""
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    categories = dict(
        ix.conn.execute(
            "SELECT category, COUNT(*) FROM docs "
            "WHERE primitive = 'item' AND broken = 0 GROUP BY category"
        )
    )
    reports_total = _scalar(ix, "SELECT COUNT(*) FROM reports")
    untriaged = _scalar(
        ix,
        "SELECT COUNT(*) FROM reports p WHERE NOT EXISTS "
        "(SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id)",
    )
    counts = {
        "items": {
            "total": sum(categories.values()),
            "by_category": {name: categories.get(name, 0) for name in CATEGORIES},
        },
        "checks": _scalar(ix, "SELECT COUNT(*) FROM checks"),
        "runs": _scalar(ix, "SELECT COUNT(*) FROM runs"),
        "reports": {"total": reports_total, "untriaged": untriaged},
        "triaged": _scalar(ix, "SELECT COUNT(*) FROM triaged_reports"),
        "approvals": _scalar(ix, "SELECT COUNT(*) FROM approvals"),
    }
    approvals = [
        {"approval": record_id, "target": target, "at": at, "confirmer": confirmer}
        for record_id, target, at, confirmer in ix.conn.execute(
            """
            SELECT a.record_id, a.target, a.at, a.confirmer FROM approvals a
            WHERE NOT EXISTS (
                SELECT 1 FROM approvals b WHERE b.target = a.target
                  AND (b.at > a.at OR (b.at = a.at AND b.record_id > a.record_id))
            )
            ORDER BY a.at DESC, a.record_id DESC LIMIT ?
            """,
            (limit,),
        )
    ]
    return {
        "counts": counts,
        "items": _item_rows(ix, limit),
        "reports": untriaged_reports(ix, limit),
        "runs": ledger(ix, limit=limit)["runs"],
        "approvals": approvals,
    }


def digest_rows(
    ix: Index, since: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Bounded activity feeds plus complete counts and usage for the digest."""
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    since = _since(since)
    clause, params = _run_clause(None, None, since)
    at_filter = "" if since is None else " WHERE at >= ?"
    at_params: list[Any] = [] if since is None else [since]
    review_where = (
        ""
        if since is None
        else " WHERE d.record_at IS NOT NULL AND d.record_at >= ?"
    )
    review_and = (
        ""
        if since is None
        else " AND d.record_at IS NOT NULL AND d.record_at >= ?"
    )
    review_params: list[Any] = [] if since is None else [_iso_ms(since)]

    untriaged = _scalar(
        ix,
        "SELECT COUNT(*) FROM reports p WHERE NOT EXISTS "
        "(SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id)"
        + ("" if since is None else " AND p.at >= ?"),
        at_params,
    )
    triaged = _scalar(
        ix,
        "SELECT COUNT(*) FROM triaged_reports t JOIN reports p "
        "ON p.record_id = t.report_id" + ("" if since is None else " WHERE p.at >= ?"),
        at_params,
    )
    counts = {
        "runs": _scalar(ix, f"SELECT COUNT(*) FROM runs r WHERE {clause}", params),
        "reports": _scalar(ix, f"SELECT COUNT(*) FROM reports{at_filter}", at_params),
        "untriaged_reports": untriaged,
        "triaged": triaged,
        "verdicts": _scalar(
            ix, f"SELECT COUNT(*) FROM verdicts{at_filter}", at_params
        ),
        "reviews": _scalar(
            ix,
            "SELECT COUNT(*) FROM reviews r JOIN docs d ON d.path = r.path"
            + review_where,
            review_params,
        ),
        "approvals": _scalar(
            ix, f"SELECT COUNT(*) FROM approvals{at_filter}", at_params
        ),
    }
    verdict_passed = _scalar(
        ix,
        "SELECT COUNT(*) FROM verdicts WHERE passed = 1" + _and(since),
        at_params,
    )
    verdict_failed = _scalar(
        ix,
        "SELECT COUNT(*) FROM verdicts WHERE passed = 0" + _and(since),
        at_params,
    )
    review_approve = _scalar(
        ix,
        "SELECT COUNT(*) FROM reviews r JOIN docs d ON d.path = r.path "
        "WHERE r.verdict = 'approve'" + review_and,
        review_params,
    )
    review_reject = _scalar(
        ix,
        "SELECT COUNT(*) FROM reviews r JOIN docs d ON d.path = r.path "
        "WHERE r.verdict = 'reject'" + review_and,
        review_params,
    )
    accepted = _scalar(
        ix, f"SELECT COUNT(*) FROM runs r WHERE {clause} AND r.accepted = 1", params
    )
    rejected = _scalar(
        ix, f"SELECT COUNT(*) FROM runs r WHERE {clause} AND r.accepted = 0", params
    )
    pending = _scalar(
        ix, f"SELECT COUNT(*) FROM runs r WHERE {clause} AND r.accepted IS NULL", params
    )
    reports = [
        {
            "report": record_id,
            "item": item,
            "run": run_id,
            "target_class": target_class,
            "target": target,
            "fingerprint": fingerprint,
            "impact": impact,
            "nothing": bool(nothing),
            "at": at,
        }
        for (
            record_id,
            item,
            run_id,
            target_class,
            target,
            fingerprint,
            impact,
            nothing,
            at,
        ) in ix.conn.execute(
            "SELECT p.record_id, p.item, p.run_id, p.target_class, p.target, "
            'p.fingerprint, p.impact, p."nothing", p.at FROM reports p'
            + at_filter
            + " ORDER BY p.at DESC, p.record_id DESC LIMIT ?",
            [*at_params, limit],
        )
    ]
    return {
        "since": since,
        "counts": counts,
        "quality": {
            "verdicts": {"passed": verdict_passed, "failed": verdict_failed},
            "reviews": {"approve": review_approve, "reject": review_reject},
            "runs": {"accepted": accepted, "rejected": rejected, "pending": pending},
        },
        "usage": _visible_usage(_attempt_usages(ix, clause, params)),
        "runs": ledger(ix, since=since, limit=limit)["runs"],
        "reports": reports,
        "items": _item_rows(ix, limit),
    }


def _run_clause(
    item: str | None, profile: str | None, since: str | None
) -> tuple[str, list[Any]]:
    where = ["1 = 1"]
    params: list[Any] = []
    if item is not None:
        where.append("r.item = ?")
        params.append(item)
    if profile is not None:
        where.append("r.profile = ?")
        params.append(profile)
    if since is not None:
        where.append('r."end" >= ?')
        params.append(since)
    return " AND ".join(where), params


def _attempt_usages(ix: Index, clause: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    rows = ix.conn.execute(
        "SELECT a.usage_json FROM run_attempts a WHERE a.run_id IN "
        f"(SELECT r.record_id FROM runs r WHERE {clause}) "
        "ORDER BY a.run_id, a.number",
        params,
    )
    return [json.loads(usage) for (usage,) in rows]


def _usage_for_runs(ix: Index, run_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not run_ids:
        return grouped
    rows = ix.conn.execute(
        "SELECT run_id, usage_json FROM run_attempts WHERE run_id IN "
        f"({', '.join('?' * len(run_ids))}) ORDER BY run_id, number",
        list(run_ids),
    )
    for run_id, usage in rows:
        grouped[run_id].append(json.loads(usage))
    return grouped


def _visible_usage(usages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Known sums and unknown counts, without turning an unknown into zero."""
    known = dict.fromkeys(USAGE_KEYS, 0)
    reported: set[str] = set()
    unknown = dict.fromkeys(USAGE_KEYS, 0)
    for raw in usages:
        for key in USAGE_KEYS:
            value = raw.get(key, "unknown") if isinstance(raw, dict) else "unknown"
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                unknown[key] += 1
            else:
                known[key] += value
                reported.add(key)
    return {
        "known_sum": {key: known[key] for key in USAGE_KEYS if key in reported},
        "unknown_attempts": {key: count for key, count in unknown.items() if count},
    }


def _item_rows(ix: Index, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "slug": slug,
            "title": title,
            "status": status,
            "category": category,
            "updated": updated,
            "path": path,
        }
        for slug, title, status, category, updated, path in ix.conn.execute(
            "SELECT slug, title, status, category, updated, path FROM docs "
            "WHERE primitive = 'item' AND broken = 0 "
            "ORDER BY updated DESC, slug ASC, path ASC LIMIT ?",
            (limit,),
        )
    ]


def _scalar(ix: Index, sql: str, params: Sequence[Any] = ()) -> int:
    return ix.conn.execute(sql, params).fetchone()[0]


def _record_data(ix: Index, path: str) -> dict[str, Any]:
    row = ix.conn.execute(
        "SELECT value_json FROM fields WHERE path = ? AND key = 'data' "
        "ORDER BY rowid LIMIT 1",
        (path,),
    ).fetchone()
    if row is None:
        return {}
    payload = json.loads(row[0])
    return payload if isinstance(payload, dict) else {}


def _since(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise QueryError("since must be a UTC timestamp")
    stamp = canonical_iso(value)
    if stamp is None:
        raise QueryError(f"since is not a timestamp: {value!r}")
    return stamp


def _iso_ms(canonical: str) -> int:
    stamp = datetime.strptime(
        canonical, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def _and(since: str | None) -> str:
    return "" if since is None else " AND at >= ?"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryError(f"{name} must be a non-empty string")
    return value


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
