"""Candidate records project into the index and answer bounded queries."""

from __future__ import annotations

from pathlib import Path

from helpers import write_candidate
from perf.gen import make_store

from context_rail.model.pack import load_pack
from context_rail.store import query
from context_rail.store.index import Index

SHA_A = "a" * 40
SHA_B = "b" * 40


def _synced(store: Path) -> Index:
    index = Index.open(store, load_pack("basic", store))
    index.sync()
    return index


def test_candidate_projects_into_index(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    write_candidate(
        store, "01JCAND000000000000000000A", "t0", SHA_A, files_changed=["src/app.py"]
    )

    index = _synced(store)
    try:
        rows = query.candidates_for(index, "t0")
    finally:
        index.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["commit"] == SHA_A
    assert row["item"] == "t0"
    assert row["actor"] == "dev" and row["attempt"] == 1 and row["seat"] is None
    assert row["record_id"] == "01JCAND000000000000000000A"
    assert row["data"]["files_changed"] == ["src/app.py"]


def test_candidate_feeds_code_touch(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    write_candidate(
        store,
        "01JCAND000000000000000000A",
        "t0",
        SHA_A,
        files_changed=["src/app.py", "docs/x.md"],
    )

    index = _synced(store)
    try:
        touched = query.code_touches(index, "src/app.py")
        other = query.code_touches(index, "docs/x.md")
    finally:
        index.close()

    assert [row["item"] for row in touched] == ["t0"]
    assert touched[0]["record"] == "01JCAND000000000000000000A"
    assert touched[0]["symbol"] is None
    assert [row["item"] for row in other] == ["t0"]


def test_candidate_queries_order_and_count(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    write_candidate(store, "01JCAND000000000000000000A", "t0", SHA_A, at="2026-11-12T10:00:00Z")
    write_candidate(
        store, "01JCAND000000000000000000B", "t0", SHA_B, attempt=2, at="2026-11-12T11:00:00Z"
    )
    write_candidate(store, "01JCAND000000000000000000C", "t1", SHA_A, at="2026-11-12T12:00:00Z")

    index = _synced(store)
    try:
        newest_first = [row["commit"] for row in query.candidates_for(index, "t0")]
        latest = query.latest_candidate(index, "t0")
        by_commit = query.candidate_for_commit(index, "t0", SHA_A)
        missing = query.candidate_for_commit(index, "t0", "c" * 40)
        nothing = query.latest_candidate(index, "t9")
        counts = (query.candidate_count(index, "t0"), query.candidate_count(index, "t9"))
        limited = query.candidates_for(index, "t0", limit=1)
    finally:
        index.close()

    assert newest_first == [SHA_B, SHA_A]
    assert latest is not None and latest["commit"] == SHA_B and latest["attempt"] == 2
    assert by_commit is not None and by_commit["record_id"] == "01JCAND000000000000000000A"
    assert missing is None and nothing is None
    assert counts == (2, 0)
    assert [row["commit"] for row in limited] == [SHA_B]


def test_deleting_a_candidate_record_drops_its_rows(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    path = write_candidate(
        store, "01JCAND000000000000000000A", "t0", SHA_A, files_changed=["src/app.py"]
    )
    index = _synced(store)
    try:
        assert query.candidate_count(index, "t0") == 1
        path.unlink()
        index.sync()

        assert query.candidate_count(index, "t0") == 0
        assert query.code_touches(index, "src/app.py") == []
    finally:
        index.close()


def test_candidate_with_an_invalid_payload_is_flagged_not_projected(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    write_candidate(store, "01JCAND000000000000000000A", "t0", "short-sha")

    index = _synced(store)
    try:
        count = query.candidate_count(index, "t0")
        problems = index.conn.execute(
            "SELECT problems FROM docs WHERE slug = ?", ("01JCAND000000000000000000A",)
        ).fetchone()
    finally:
        index.close()

    assert count == 0
    assert problems is not None and "invalid_payload" in problems[0]
