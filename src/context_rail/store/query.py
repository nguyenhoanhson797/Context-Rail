"""Bounded reads over the index: find, show, records and activity.

Every query here runs as SQL over the index. Only ``show`` opens a store file,
and only the requested document; nothing in this module launches a process,
hashes a source tree or parses documents it was not asked for.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from context_rail.core.util import canonical_iso
from context_rail.kernel.budgets import CandidateFacts, usage_totals
from context_rail.kernel.seats import LiveSeat, fold
from context_rail.kernel.triage import AttemptFacts
from context_rail.model.primitives import CATEGORIES, PRIMITIVES, RELATIONS
from context_rail.model.seats import SeatEvent
from context_rail.store.doc import parse_doc
from context_rail.store.index import Index

__all__ = [
    "QueryError",
    "approval_for",
    "brief_rows",
    "candidate_by_record",
    "candidate_count",
    "candidate_facts",
    "candidate_for_commit",
    "candidates_for",
    "check_state",
    "check_states",
    "code_touches",
    "digest_rows",
    "execution_count",
    "execution_latest",
    "find",
    "human_proposals",
    "human_wait_reports",
    "items_for_migration",
    "last_activity_ms",
    "latest_candidate",
    "ledger",
    "live_seats",
    "pack_change_for",
    "records_about",
    "rejected_reviews",
    "count_reports_by_fingerprint",
    "report_for_candidate",
    "reports_by_fingerprint",
    "result_adoption_for",
    "result_by_record",
    "result_count",
    "results_for_item",
    "reversible_decisions",
    "review_for",
    "review_for_item",
    "review_for_result",
    "seat_events",
    "show",
    "totals_for_candidates",
    "attempt_facts",
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

_ITEM_COLUMNS = "d.slug, d.title, d.primitive, d.preset, d.workflow, d.status, d.path"

_DOC_COLUMNS = (
    "slug, path, primitive, preset, workflow, title, status, category, created, "
    "updated, etag, digest, problems"
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
    allow: Callable[[str, str, str | None], bool] | None = None,
) -> dict[str, Any]:
    """Page through documents matching every supplied filter.

    Results are ordered by ``updated`` descending, then slug, then path. The
    total counts each document once no matter how many of its rows match, and
    ``omitted`` reports what the page left behind. ``allow`` filters a
    restricted caller's matches by slug, path and preset before paging, and the
    result then also says how many matches the reach alone left out.
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
        if allow is None:
            total = ix.conn.execute(
                f"SELECT COUNT(DISTINCT d.path) FROM docs d WHERE {clause}", params
            ).fetchone()[0]
            rows = ix.conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM docs d WHERE {clause} "
                "ORDER BY d.updated DESC, d.slug ASC, d.path ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
            outside_reach = 0
        else:
            candidates = ix.conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM docs d WHERE {clause} "
                "ORDER BY d.updated DESC, d.slug ASC, d.path ASC",
                params,
            ).fetchall()
            kept = [
                row for row in candidates if allow(row[0], row[6], row[3])
            ]
            total = len(kept)
            rows = kept[:limit]
            outside_reach = len(candidates) - total
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
            "workflow": workflow,
            "status": state,
            "path": path,
        }
        for slug, title, kind, preset_name, workflow, state, path in rows
    ]
    result = {"items": items, "total": total, "omitted": total - len(items)}
    if allow is not None:
        result["omitted_outside_reach"] = outside_reach
    return result


def show(
    ix: Index, slug: str, *, path: str | None = None, body_chars: int = DEFAULT_BODY_CHARS
) -> dict:
    """One document with its relations and a bounded body.

    ``path`` selects one document when several share a slug; without it a
    shared slug is refused. Raises ``LookupError("not_found")`` when nothing
    matches and ``LookupError("ambiguous")`` for a shared slug.
    """
    body_chars = _bounded(body_chars, BODY_CHARS_BOUNDS, "body_chars")
    slug = _slug(slug)

    if path is not None:
        if not isinstance(path, str) or not path.strip():
            raise LookupError("not_found")
        path = path.strip()
        found = ix.conn.execute(
            "SELECT path FROM docs WHERE path = ? AND slug = ?", (path, slug)
        ).fetchone()
        if found is None:
            raise LookupError("not_found")
    else:
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
        workflow,
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

    try:
        body = parse_doc(path, (ix.store / path).read_bytes()).body
    except FileNotFoundError:
        # A document can vanish between the index sync and the body read.
        raise LookupError("not_found") from None
    visible = body[:body_chars]
    return {
        "doc": {
            "slug": found_slug,
            "path": found_path,
            "primitive": primitive,
            "preset": preset,
            "workflow": workflow,
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
                "slug": record_slug,
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
        "store_binding": None,
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
                "store_binding": payload.get("store"),
            }
        )
    return state


def code_touches(
    ix: Index, path: str, symbol: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Candidates that changed ``path``, newest first, one row per candidate.

    A candidate records changed files, not symbols, so a file-level touch also
    answers for any symbol inside that file.
    """
    path = _text(path, "path")
    limit = _bounded(limit, LIMIT_BOUNDS, "limit")
    if symbol is None:
        rows = ix.conn.execute(
            "SELECT item, record, at FROM code_touch WHERE path = ? "
            "GROUP BY record ORDER BY at DESC, record DESC LIMIT ?",
            (path, limit),
        ).fetchall()
        return [
            {"item": item, "record": record, "at": at, "symbol": None}
            for item, record, at in rows
        ]
    symbol = _text(symbol, "symbol")
    rows = ix.conn.execute(
        "SELECT item, record, at FROM code_touch WHERE path = ? "
        "AND (symbol = ? OR symbol IS NULL) "
        "GROUP BY record ORDER BY at DESC, record DESC LIMIT ?",
        (path, symbol, limit),
    ).fetchall()
    return [
        {"item": item, "record": record, "at": at, "symbol": symbol}
        for item, record, at in rows
    ]


_CANDIDATE_SELECT = (
    "SELECT record_id, item, commit_sha, base, attempt, actor, seat, at, data_json "
    "FROM candidates"
)
_CANDIDATE_ORDER = "ORDER BY at DESC, attempt DESC, record_id DESC"


def candidates_for(ix: Index, item: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Candidate commits recorded for ``item``, newest first."""
    item = _text(item, "item")
    limit = _bounded(limit, LIMIT_BOUNDS, "limit")
    rows = ix.conn.execute(
        f"{_CANDIDATE_SELECT} WHERE item = ? {_CANDIDATE_ORDER} LIMIT ?", (item, limit)
    ).fetchall()
    return [_candidate_row(row) for row in rows]


def candidate_for_commit(ix: Index, item: str, commit: str) -> dict[str, Any] | None:
    """The newest candidate record of ``item`` for exactly ``commit``, or None."""
    item = _text(item, "item")
    commit = _text(commit, "commit")
    row = ix.conn.execute(
        f"{_CANDIDATE_SELECT} WHERE item = ? AND commit_sha = ? {_CANDIDATE_ORDER} LIMIT 1",
        (item, commit),
    ).fetchone()
    return None if row is None else _candidate_row(row)


def latest_candidate(ix: Index, item: str) -> dict[str, Any] | None:
    """The newest candidate recorded for ``item``, or None."""
    rows = candidates_for(ix, item, limit=1)
    return rows[0] if rows else None


def candidate_by_record(ix: Index, record_id: str) -> dict[str, Any] | None:
    """The candidate a record id names, or None."""
    record_id = _text(record_id, "record")
    row = ix.conn.execute(
        f"{_CANDIDATE_SELECT} WHERE record_id = ?", (record_id,)
    ).fetchone()
    return None if row is None else _candidate_row(row)


def candidate_facts(ix: Index, item: str | None = None) -> list[CandidateFacts]:
    """What each recorded candidate says it cost, for one item or every item."""
    if item is None:
        rows = ix.conn.execute(
            "SELECT item, data_json FROM candidates ORDER BY at, record_id"
        ).fetchall()
    else:
        rows = ix.conn.execute(
            "SELECT item, data_json FROM candidates WHERE item = ? ORDER BY at, record_id",
            (_text(item, "item"),),
        ).fetchall()
    facts: list[CandidateFacts] = []
    for owner, data_json in rows:
        data = json.loads(data_json)
        usage = data.get("usage")
        facts.append(
            CandidateFacts(
                owner,
                data.get("duration_s"),
                usage if isinstance(usage, dict) else {},
            )
        )
    return facts


def candidate_count(ix: Index, item: str) -> int:
    """How many candidates were recorded for ``item``."""
    item = _text(item, "item")
    return ix.conn.execute(
        "SELECT count(*) FROM candidates WHERE item = ?", (item,)
    ).fetchone()[0]


def _candidate_row(row: tuple[Any, ...]) -> dict[str, Any]:
    record_id, item, commit, base, attempt, actor, seat, at, data_json = row
    return {
        "record_id": record_id,
        "item": item,
        "commit": commit,
        "base": base,
        "attempt": attempt,
        "actor": actor,
        "seat": seat,
        "at": at,
        "data": json.loads(data_json),
    }


def untriaged_reports(ix: Index, limit: int = 100) -> list[dict[str, Any]]:
    """Reports no triage Record has claimed yet, newest first."""
    limit = _bounded(limit, REPORT_LIMIT_BOUNDS, "limit")
    rows = ix.conn.execute(
        f"{_REPORT_SELECT} {_UNTRIAGED} "
        "ORDER BY p.at DESC, p.record_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_report_row(row) for row in rows]


def reports_by_fingerprint(
    ix: Index, fingerprint: str, limit: int = 40
) -> list[dict[str, Any]]:
    """Every report filed under one fingerprint, newest first.

    A triaged report is claimed by its triage Record and never appears on an
    untriaged page again, so a recurrence of the same fingerprint needs this
    whole-store selection to keep the evidence an earlier triage exported.
    """
    limit = _bounded(limit, REPORT_LIMIT_BOUNDS, "limit")
    rows = ix.conn.execute(
        f"{_REPORT_SELECT} WHERE p.fingerprint = ? "
        "ORDER BY p.at DESC, p.record_id DESC LIMIT ?",
        (fingerprint, limit),
    ).fetchall()
    return [_report_row(row) for row in rows]


def count_reports_by_fingerprint(ix: Index, fingerprint: str) -> int:
    """How many reports one fingerprint has on file, counted in the index.

    A bounded read cannot report what it never fetched, so an export's omission
    count comes from here rather than from the page it happens to hold.
    """
    row = ix.conn.execute(
        "SELECT COUNT(*) FROM reports WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    return 0 if row is None else int(row[0])


#: The report columns every report feed returns, in one place.
_REPORT_SELECT = (
    "SELECT p.record_id, p.item, p.run_id, p.candidate_id, p.target_class, p.target, "
    'p.fingerprint, p.impact, p."nothing", p.at, p.data_json, p.path FROM reports p'
)
_UNTRIAGED_ONLY = (
    "NOT EXISTS (SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id)"
)
_UNTRIAGED = f"WHERE {_UNTRIAGED_ONLY}"


def _report_row(row: Sequence[Any]) -> dict[str, Any]:
    """One indexed report row as the views read it."""
    (
        record_id,
        item,
        run_id,
        candidate_id,
        target_class,
        target,
        fingerprint,
        impact,
        nothing,
        at,
        data,
        path,
    ) = row
    return {
        "report": record_id,
        "record_id": record_id,
        "item": item,
        "run": run_id,
        "candidate": candidate_id,
        "target_class": target_class,
        "target": target,
        "fingerprint": fingerprint,
        "impact": impact,
        "nothing": bool(nothing),
        "at": at,
        "data": json.loads(data),
        "path": path,
    }


def human_wait_reports(ix: Index, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """Untriaged human-wait reports, newest first, plus how many exist in total.

    A wait is its own query over the ``(target_class, at)`` index, never a
    filter over a page of newest reports: a wait older than that page still
    needs the human, and the total is what a truthful omission count needs.
    """
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    where = f"WHERE p.target_class = 'human-wait' AND {_UNTRIAGED_ONLY}"
    rows = ix.conn.execute(
        f"{_REPORT_SELECT} {where} ORDER BY p.at DESC, p.record_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    total = _scalar(ix, f"SELECT COUNT(*) FROM reports p {where}")
    return [_report_row(row) for row in rows], total


def attempt_facts(
    ix: Index, candidate_ids: Sequence[str] | None = None
) -> list[AttemptFacts]:
    """Whether each recorded candidate failed, from reviews and Check verdicts.

    A candidate failed when its newest review rejected it or a Check failed on
    exactly its commit. Reading it here keeps triage's weight a fact of the
    index, never of a report's prose.
    """
    if candidate_ids is None:
        rows = ix.conn.execute(
            "SELECT record_id, item, commit_sha FROM candidates "
            "ORDER BY at DESC, record_id DESC LIMIT ?",
            (REPORT_LIMIT_BOUNDS[1],),
        ).fetchall()
    else:
        ids = sorted({value for value in candidate_ids if isinstance(value, str) and value})
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = ix.conn.execute(
            "SELECT record_id, item, commit_sha FROM candidates "
            f"WHERE record_id IN ({marks}) ORDER BY record_id",
            ids,
        ).fetchall()
    found: list[AttemptFacts] = []
    for record_id, item, commit in rows:
        latest = ix.conn.execute(
            "SELECT r.verdict FROM reviews r JOIN docs d ON d.path = r.path "
            "WHERE r.item = ? AND r.candidate_commit = ? "
            "ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1",
            (item, commit),
        ).fetchone()
        rejected = latest is not None and latest[0] == "reject"
        failing = ix.conn.execute(
            "SELECT 1 FROM verdicts WHERE passed = 0 AND candidate_commit = ? LIMIT 1",
            (commit,),
        ).fetchone()
        found.append(AttemptFacts(record_id, failed=rejected or failing is not None))
    return found


def report_for_candidate(ix: Index, candidate_id: str) -> str | None:
    """The Record id of the report already bound to a candidate record, if any."""
    row = ix.conn.execute(
        "SELECT record_id FROM reports WHERE candidate_id = ? "
        "ORDER BY at DESC, record_id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return None if row is None else row[0]


_RESULT_SELECT = (
    "SELECT path, record_id, item, at, actor, outcome, candidate_commit, "
    "body_hash, item_digest, work_cycle, contract_digest, valid, problem, data_json "
    "FROM results"
)
_RESULT_ORDER = "ORDER BY order_at DESC, record_id DESC"


def result_by_record(ix: Index, record_id: str) -> dict[str, Any] | None:
    """One projected producer result by its ``record_id``, or None."""
    record_id = _text(record_id, "record")
    row = ix.conn.execute(
        f"{_RESULT_SELECT} WHERE record_id = ? LIMIT 1", (record_id,)
    ).fetchone()
    return None if row is None else _result_row(row)


def results_for_item(
    ix: Index, item: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Producer results for ``item``, ordered by envelope ``(at DESC, record_id DESC)``."""
    item = _text(item, "item")
    limit = _bounded(limit, LIMIT_BOUNDS, "limit")
    rows = ix.conn.execute(
        f"{_RESULT_SELECT} WHERE item = ? {_RESULT_ORDER} LIMIT ?", (item, limit)
    ).fetchall()
    return [_result_row(row) for row in rows]


def result_count(ix: Index, item: str) -> int:
    """How many producer results (including invalid diagnostic stubs) exist for ``item``."""
    item = _text(item, "item")
    row = ix.conn.execute(
        "SELECT COUNT(*) FROM results WHERE item = ?", (item,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


def result_adoption_for(
    ix: Index, item: str, workflow: str | None = None
) -> dict[str, Any] | None:
    """The newest ``results.adopted`` snapshot entry for ``item`` (optionally matching ``workflow``)."""
    item = _text(item, "item")
    if workflow is None:
        row = ix.conn.execute(
            "SELECT path, record_id, item, workflow, item_digest, work_cycle, at, data_json "
            "FROM result_adoptions WHERE item = ? ORDER BY at DESC, record_id DESC LIMIT 1",
            (item,),
        ).fetchone()
    else:
        row = ix.conn.execute(
            "SELECT path, record_id, item, workflow, item_digest, work_cycle, at, data_json "
            "FROM result_adoptions WHERE item = ? AND workflow = ? "
            "ORDER BY at DESC, record_id DESC LIMIT 1",
            (item, _text(workflow, "workflow")),
        ).fetchone()
    if row is None:
        return None
    path, record_id, owner, wf, item_digest, cycle, at, data_json = row
    return {
        "path": path,
        "record_id": record_id,
        "item": owner,
        "workflow": wf,
        "item_digest": item_digest,
        "work_cycle": cycle,
        "at": at,
        "data": json.loads(data_json),
    }


def _result_row(row: Sequence[Any]) -> dict[str, Any]:
    (
        path,
        record_id,
        item,
        at,
        actor,
        outcome,
        candidate_commit,
        body_hash,
        item_digest,
        work_cycle,
        contract_digest,
        valid,
        problem,
        data_json,
    ) = row
    return {
        "record_id": record_id,
        "path": path,
        "item": item,
        "at": at,
        "actor": actor,
        "outcome": outcome,
        "candidate_commit": candidate_commit,
        "body_hash": body_hash,
        "item_digest": item_digest,
        "work_cycle": work_cycle,
        "contract_digest": contract_digest,
        "valid": bool(valid),
        "problem": problem,
        "data": json.loads(data_json),
    }


def review_for(ix: Index, candidate: str) -> dict[str, Any] | None:
    """The newest review bound to exactly ``candidate``, or None."""
    candidate = _text(candidate, "candidate")
    row = ix.conn.execute(
        """
        SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.result_record,
               r.result_body_hash, r.reviewer, r.verdict, r.data_json, r.path
        FROM reviews r JOIN docs d ON d.path = r.path
        WHERE r.candidate_commit = ?
        ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1
        """,
        (candidate,),
    ).fetchone()
    if row is None:
        return None
    (
        record_id,
        item,
        run_id,
        bound,
        result_record,
        result_body_hash,
        reviewer,
        verdict,
        data_json,
        path,
    ) = row
    payload = json.loads(data_json)
    return {
        "review": record_id,
        "record_id": record_id,
        "item": item,
        "run": run_id,
        "candidate_commit": bound,
        "result_record": result_record,
        "result_body_hash": result_body_hash,
        "reviewer": reviewer,
        "verdict": verdict,
        "findings": payload.get("findings", []),
        "evidence": payload.get("evidence", []),
        "data": payload,
        "path": path,
    }


def review_for_result(
    ix: Index, item: str, result_record: str
) -> dict[str, Any] | None:
    """The newest review bound to ``(item, result_record)``, or None."""
    item = _text(item, "item")
    result_record = _text(result_record, "result_record")
    row = ix.conn.execute(
        """
        SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.result_record,
               r.result_body_hash, r.reviewer, r.verdict, r.data_json, r.path
        FROM reviews r JOIN docs d ON d.path = r.path
        WHERE r.item = ? AND r.result_record = ?
        ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1
        """,
        (item, result_record),
    ).fetchone()
    if row is None:
        return None
    (
        record_id,
        owner,
        run_id,
        bound,
        res_id,
        res_hash,
        reviewer,
        verdict,
        data_json,
        path,
    ) = row
    payload = json.loads(data_json)
    return {
        "review": record_id,
        "record_id": record_id,
        "item": owner,
        "run": run_id,
        "candidate_commit": bound,
        "result_record": res_id,
        "result_body_hash": res_hash,
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
        SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.result_record,
               r.result_body_hash, r.reviewer, r.verdict, r.data_json, r.path
        FROM reviews r JOIN docs d ON d.path = r.path
        WHERE r.item = ?
        ORDER BY d.record_at DESC, r.record_id DESC LIMIT 1
        """,
        (item,),
    ).fetchone()
    if row is None:
        return None
    (
        record_id,
        owner,
        run_id,
        bound,
        result_record,
        result_body_hash,
        reviewer,
        verdict,
        data_json,
        path,
    ) = row
    payload = json.loads(data_json)
    return {
        "review": record_id,
        "record_id": record_id,
        "item": owner,
        "run": run_id,
        "candidate_commit": bound,
        "result_record": result_record,
        "result_body_hash": result_body_hash,
        "reviewer": reviewer,
        "verdict": verdict,
        "findings": payload.get("findings", []),
        "evidence": payload.get("evidence", []),
        "data": payload,
        "path": path,
    }


def items_for_migration(ix: Index) -> list[dict[str, Any]]:
    """Every indexed Item with its preset, status and frontmatter fields."""
    rows = ix.conn.execute(
        "SELECT path, slug, preset, workflow, status FROM docs "
        "WHERE primitive = 'item' AND broken = 0 ORDER BY path"
    ).fetchall()
    items: list[dict[str, Any]] = []
    for path, slug, preset, workflow, status in rows:
        fields: dict[str, Any] = {}
        for key, value_json in ix.conn.execute(
            "SELECT key, value_json FROM fields WHERE path = ? ORDER BY rowid",
            (path,),
        ):
            try:
                fields[key] = json.loads(value_json)
            except (TypeError, ValueError):
                continue
        items.append(
            {
                "path": path,
                "slug": slug,
                "preset": preset,
                "workflow": workflow,
                "status": status,
                "fields": fields,
            }
        )
    return items


def pack_change_for(ix: Index, target: str) -> dict[str, Any] | None:
    """The newest ``pack.changed`` whose ``approval_target`` is ``target``."""
    target = _text(target, "target")
    rows = ix.conn.execute(
        "SELECT d.path, d.slug, d.record_at FROM docs d "
        "JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "WHERE k.value_json = ? ORDER BY d.record_at DESC, d.slug DESC",
        (json.dumps("pack.changed"),),
    ).fetchall()
    for path, slug, at in rows:
        data = record_data(ix, path)
        if data.get("approval_target") != target:
            continue
        return {"record_id": slug, "at": at, "path": path, "data": data}
    return None


def approval_records(ix: Index, limit: int = 200) -> list[dict[str, Any]]:
    """Every approval record, newest first, with its stored data."""
    rows = ix.conn.execute(
        "SELECT record_id, target, at, path FROM approvals "
        "ORDER BY at DESC, record_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    found: list[dict[str, Any]] = []
    for record_id, target, at, path in rows:
        found.append(
            {
                "record_id": record_id,
                "target": target,
                "at": at,
                "data": record_data(ix, path),
            }
        )
    return found


def seat_events(ix: Index) -> list[SeatEvent]:
    """Every projected seat grant and revoke, oldest first by ``(at, id)``."""
    rows = ix.conn.execute(
        "SELECT kind, seat, role, anchor, profile, actor_by, at, record_id "
        "FROM seats ORDER BY at, record_id"
    ).fetchall()
    return [SeatEvent(*row) for row in rows]


def live_seats(ix: Index) -> dict[str, LiveSeat]:
    """Seats still granted after folding every grant and revoke."""
    return fold(seat_events(ix))


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
    payload = record_data(ix, path)
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


def reversible_decisions(
    ix: Index, since: str | None = None, limit: int = 20
) -> tuple[list[dict[str, Any]], int]:
    """Newest reversible decisions, bounded, plus how many the filter matched.

    The class lives in the Record's data, never in its kind, so this reads the
    payload and re-checks the class it found instead of trusting the filter. The
    count is what a view needs to report an honest omission, so it is read from
    the same predicate rather than inferred from the truncated page.
    """
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    since = _since(since)
    clause = (
        "WHERE d.primitive = 'record' AND d.broken = 0 "
        "AND json_extract(f2.value_json, '$.decision_class') = ?"
    )
    params: list[Any] = ["cto-reversible"]
    if since is not None:
        clause += " AND d.record_at IS NOT NULL AND d.record_at >= ?"
        params.append(_iso_ms(since))
    joins = (
        "FROM docs d "
        "JOIN fields f1 ON f1.path = d.path AND f1.key = 'kind' "
        " AND f1.value_json = '\"decision.made\"' "
        "JOIN fields f2 ON f2.path = d.path AND f2.key = 'data' "
    )
    rows = ix.conn.execute(
        "SELECT d.slug, d.path, d.record_at, f2.value_json, "
        "(SELECT group_concat(r.dst, ',') FROM relations r "
        " WHERE r.src_path = d.path AND r.rel = 'about') "
        f"{joins}{clause} ORDER BY d.record_at DESC, d.slug DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    found: list[dict[str, Any]] = []
    for slug, path, at, payload_json, about in rows:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict) or payload.get("decision_class") != "cto-reversible":
            continue
        found.append(
            {
                "decision": slug,
                "record_id": slug,
                "class": payload.get("decision_class"),
                "reason": payload.get("reason"),
                "before": payload.get("before"),
                "after": payload.get("after"),
                "item": (about or "").split(",")[0] or None,
                "about": [name for name in (about or "").split(",") if name],
                "at": None if at is None else _iso(at),
                "path": path,
            }
        )
    return found, _scalar(ix, f"SELECT COUNT(*) {joins}{clause}", params)


def human_proposals(ix: Index, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """Open proposal Notes whose decision class waits on a human, and their total."""
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    where = (
        "WHERE d.primitive = 'note' AND d.broken = 0 "
        "AND EXISTS (SELECT 1 FROM fields c WHERE c.path = d.path "
        "AND c.key = 'decision_class' AND c.value_json = '\"human-only\"')"
    )
    rows = ix.conn.execute(
        "SELECT d.slug, d.title, d.updated, p.value_json FROM docs d "
        "JOIN fields f ON f.path = d.path AND f.key = 'decision_class' "
        " AND f.value_json = '\"human-only\"' "
        "LEFT JOIN fields p ON p.path = d.path AND p.key = 'proposed_change' "
        f"{where} ORDER BY d.updated DESC, d.slug DESC LIMIT ?",
        (limit,),
    ).fetchall()
    found = [
        {
            "slug": slug,
            "item": slug,
            "title": title,
            "proposed_change": None if change is None else json.loads(change),
            "updated": updated,
        }
        for slug, title, updated, change in rows
    ]
    return found, _scalar(ix, f"SELECT COUNT(*) FROM docs d {where}")


def review_by_record(ix: Index, record_id: str) -> dict[str, Any] | None:
    """The review a record id names, or None."""
    record_id = _text(record_id, "record")
    row = ix.conn.execute(
        "SELECT item, candidate_commit, result_record, verdict, path FROM reviews WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    item, commit, result_record, verdict, path = row
    return {
        "record_id": record_id,
        "item": item,
        "commit": commit,
        "result_record": result_record,
        "verdict": verdict,
        "path": path,
    }


def rejected_reviews(
    ix: Index, since: str | None = None, limit: int = 20
) -> tuple[list[dict[str, Any]], int]:
    """Newest rejecting reviews, bounded, plus how many the filter matched.

    The count comes from the same predicate, so a view can report the trimming
    it actually did instead of a remainder computed from a page that was
    already cut.
    """
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    since = _since(since)
    clause = "WHERE r.verdict = 'reject'"
    params: list[Any] = []
    if since is not None:
        clause += " AND d.record_at IS NOT NULL AND d.record_at >= ?"
        params.append(_iso_ms(since))
    joins = "FROM reviews r JOIN docs d ON d.path = r.path "
    rows = ix.conn.execute(
        "SELECT r.record_id, r.item, r.run_id, r.candidate_commit, r.reviewer, "
        f"d.record_at {joins}{clause} "
        "ORDER BY d.record_at DESC, r.record_id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    found = [
        {
            "review": record_id,
            "record_id": record_id,
            "item": item,
            "run": run_id,
            "candidate_commit": candidate,
            "reviewer": reviewer,
            "at": None if at is None else _iso(at),
        }
        for record_id, item, run_id, candidate, reviewer, at in rows
    ]
    return found, _scalar(ix, f"SELECT COUNT(*) {joins}{clause}", params)


def execution_latest(
    ix: Index,
    *,
    actions: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """The newest attempt per ``(action, target)``, never merged with status."""
    where: list[str] = []
    params: list[Any] = []
    if actions is not None:
        if not actions:
            return []
        where.append(f"action IN ({', '.join('?' * len(actions))})")
        params.extend(actions)
    if targets is not None:
        if not targets:
            return []
        where.append(f"target IN ({', '.join('?' * len(targets))})")
        params.extend(targets)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = ix.conn.execute(
        "SELECT action, target, state, attempt, started_at, completed_at, "
        "outputs_json, error, actor, tool, params_json, runner_digest, record_id "
        f"FROM executions {clause} "
        "ORDER BY target, action, attempt DESC, completed_at DESC, record_id DESC",
        params,
    ).fetchall()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row[0], row[1])
        if key in latest:
            continue
        latest[key] = {
            "action": row[0],
            "target": row[1],
            "state": row[2],
            "attempt": row[3],
            "started_at": row[4],
            "completed_at": row[5],
            "outputs": json.loads(row[6]),
            "error": row[7],
            "actor": row[8],
            "tool": row[9],
            "params": json.loads(row[10]),
            "runner_digest": row[11],
            "record": row[12],
        }
    return [latest[key] for key in sorted(latest)]


def execution_count(ix: Index, action: str, target: str) -> int:
    """How many attempts one action has recorded on one target."""
    row = ix.conn.execute(
        "SELECT COUNT(*) FROM executions WHERE action = ? AND target = ?",
        (action, target),
    ).fetchone()
    return int(row[0]) if row is not None else 0


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
        "candidates": _scalar(ix, "SELECT COUNT(*) FROM candidates"),
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
        "candidates": ledger(ix, limit=limit)["candidates"],
        "approvals": approvals,
    }


def digest_rows(
    ix: Index, since: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Bounded activity feeds plus complete counts and usage for the digest."""
    limit = _bounded(limit, FEED_LIMIT_BOUNDS, "limit")
    since = _since(since)
    clause, params = _candidate_clause(None, None, since)
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
        "candidates": _scalar(ix, f"SELECT COUNT(*) FROM candidates c WHERE {clause}", params),
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
        ix,
        f"SELECT COUNT(*) FROM candidates c WHERE {clause} AND {_LATEST_VERDICT} = 'approve'",
        params,
    )
    rejected = _scalar(
        ix,
        f"SELECT COUNT(*) FROM candidates c WHERE {clause} AND {_LATEST_VERDICT} = 'reject'",
        params,
    )
    pending = _scalar(
        ix,
        f"SELECT COUNT(*) FROM candidates c WHERE {clause} AND {_LATEST_VERDICT} IS NULL",
        params,
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
            "candidates": {"accepted": accepted, "rejected": rejected, "pending": pending},
        },
        "usage": _visible_usage(_candidate_usages(ix, clause, params)),
        "candidates": ledger(ix, since=since, limit=limit)["candidates"],
        "reports": reports,
        "items": _item_rows(ix, limit),
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


def record_data(ix: Index, path: str) -> dict[str, Any]:
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


def _iso(milliseconds: int) -> str:
    """The canonical UTC stamp of an indexed millisecond value."""
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


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
    if not term:
        return None
    if term.count('"') % 2:
        # Keep an unclosed phrase intact so FTS5 reports the syntax error.
        return term
    return " ".join(_fts_token(token) for token in _fts_tokens(term))


_FTS_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})


def _fts_tokens(term: str) -> list[str]:
    """Split a query into bare words, parenthesised operators and phrases."""
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    index = 0
    while index < len(term):
        char = term[index]
        if char.isspace():
            flush()
            index += 1
        elif char in "()":
            flush()
            tokens.append(char)
            index += 1
        elif char == '"':
            flush()
            end = term.find('"', index + 1)
            tokens.append(term[index:] if end < 0 else term[index : end + 1])
            index = len(term) if end < 0 else end + 1
        elif (
            char == "*"
            and not current
            and tokens
            and len(tokens[-1]) >= 2
            and tokens[-1].startswith('"')
            and tokens[-1].endswith('"')
        ):
            tokens[-1] += "*"
            index += 1
        else:
            current.append(char)
            index += 1
    flush()
    return tokens


def _fts_token(token: str) -> str:
    """Quote one ordinary token; leave FTS5 operators and phrases alone."""
    if token in _FTS_OPERATORS or token in ("(", ")"):
        return token
    if len(token) >= 3 and token.startswith('"') and token.endswith('"*'):
        return token
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return token
    if (
        token.endswith("*")
        and len(token) > 1
        and all(char.isalnum() or char == "_" for char in token[:-1])
    ):
        return f'"{token[:-1]}"*'
    if (
        token.startswith("-")
        and len(token) > 1
        and all(char.isalnum() or char == "_" for char in token[1:])
    ):
        return token
    if all(char.isalnum() or char == "_" for char in token):
        return token
    return '"' + token.replace('"', '""') + '"'


def _grouped(rows: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {}
    for name, value in rows:
        grouped.setdefault(name, set()).add(value)
    return {name: sorted(values) for name, values in grouped.items()}


#: The newest review verdict recorded for exactly one candidate's commit.
_LATEST_VERDICT = (
    "(SELECT rv.verdict FROM reviews rv JOIN docs rd ON rd.path = rv.path "
    "WHERE rv.item = c.item AND rv.candidate_commit = c.commit_sha "
    "ORDER BY rd.record_at DESC, rv.record_id DESC LIMIT 1)"
)


def ledger(
    ix: Index,
    *,
    item: str | None = None,
    profile: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Candidates with the usage they reported and their latest verdict, bounded.

    Totals cover every matching candidate, not just the page, and a counter no
    candidate reported stays out of ``known_sum`` rather than reading zero.
    """
    limit = _bounded(limit, LEDGER_LIMIT_BOUNDS, "limit")
    item = _optional_name(item, "item")
    profile = _optional_name(profile, "profile")
    since = _since(since)
    clause, params = _candidate_clause(item, profile, since)

    total = ix.conn.execute(
        f"SELECT COUNT(*) FROM candidates c WHERE {clause}", params
    ).fetchone()[0]
    rows = ix.conn.execute(
        f"""
        SELECT c.record_id, c.item, c.commit_sha, c.attempt, c.actor, c.seat, c.at,
               c.data_json, {_LATEST_VERDICT},
               (SELECT p.record_id FROM reports p WHERE p.candidate_id = c.record_id
                ORDER BY p.at DESC, p.record_id DESC LIMIT 1)
        FROM candidates c WHERE {clause}
        ORDER BY c.at DESC, c.record_id DESC LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for record_id, owner, commit, attempt, actor, seat, at, data_json, verdict, report in rows:
        data = json.loads(data_json)
        usage = data.get("usage")
        candidates.append(
            {
                "candidate": record_id,
                "record_id": record_id,
                "item": owner,
                "commit": commit,
                "attempt": attempt,
                "actor": actor,
                "seat": seat,
                "profile": data.get("profile"),
                "profile_version": data.get("profile_version"),
                "model": data.get("model"),
                "harness": data.get("harness"),
                "duration_s": data.get("duration_s"),
                "usage": usage if isinstance(usage, dict) else {},
                "verdict": verdict,
                "report": report,
                "at": at,
            }
        )
    totals = _visible_usage(_candidate_usages(ix, clause, params))
    return {
        "candidates": candidates,
        "totals": totals,
        "unknown_counts": totals["unknown_attempts"],
        "total": total,
        "omitted": total - len(candidates),
    }


def _candidate_clause(
    item: str | None, profile: str | None, since: str | None
) -> tuple[str, list[Any]]:
    where = ["1 = 1"]
    params: list[Any] = []
    if item is not None:
        where.append("c.item = ?")
        params.append(item)
    if profile is not None:
        where.append("json_extract(c.data_json, '$.profile') = ?")
        params.append(profile)
    if since is not None:
        where.append("c.at >= ?")
        params.append(since)
    return " AND ".join(where), params


def _usage_of(data_json: str) -> dict[str, Any]:
    usage = json.loads(data_json).get("usage")
    return usage if isinstance(usage, dict) else {}


def _candidate_usages(ix: Index, clause: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    rows = ix.conn.execute(
        f"SELECT c.data_json FROM candidates c WHERE {clause} ORDER BY c.at, c.record_id",
        params,
    )
    return [_usage_of(data_json) for (data_json,) in rows]


def totals_for_candidates(ix: Index, record_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Usage totals over exactly ``record_ids``, with unknown counters kept apart."""
    ids = sorted({value for value in record_ids if isinstance(value, str) and value})
    if not ids:
        return _visible_usage([])
    rows = ix.conn.execute(
        f"SELECT data_json FROM candidates WHERE record_id IN ({', '.join('?' * len(ids))}) "
        "ORDER BY record_id",
        ids,
    )
    return _visible_usage([_usage_of(data_json) for (data_json,) in rows])


def _visible_usage(usages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Known sums and unknown counts, without turning an unknown into zero."""
    maps = list(usages)
    totals = usage_totals(maps)
    reported = {
        key
        for usage in maps
        for key, value in usage.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    }
    return {
        "known_sum": {key: totals.known_sum[key] for key in totals.known_sum if key in reported},
        "unknown_attempts": {
            key: count for key, count in totals.unknown_attempts.items() if count
        },
    }
