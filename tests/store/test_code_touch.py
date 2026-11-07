"""Which runs touched which files and symbols, rebuilt from Records."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from helpers import write_run
from perf.gen import make_store

from context_rail.model.pack import load_pack
from context_rail.store import query
from context_rail.store.index import INDEX_SCHEMA, Index
from context_rail.store.location import index_path


def _synced(store: Path) -> Index:
    index = Index.open(store, load_pack("basic", store))
    index.sync()
    return index


def _store_with_runs(tmp_path: Path) -> Path:
    store = make_store(tmp_path, 10)
    write_run(
        store,
        "01JRUN000000000000000000A1",
        item="t0",
        at="2026-10-28T10:00:00Z",
        files_changed=["src/a.py", "README.md"],
        symbols_changed=["src/a.py:f", "README.md"],
    )
    write_run(
        store,
        "01JRUN000000000000000000A2",
        item="t1",
        at="2026-10-28T11:00:00Z",
        files_changed=["src/a.py"],
        symbols_changed=["src/a.py:g"],
    )
    return store


def test_runs_fill_code_touch(tmp_path: Path) -> None:
    index = _synced(_store_with_runs(tmp_path))
    try:
        by_path = query.code_touches(index, "src/a.py")
        by_symbol = query.code_touches(index, "src/a.py", "src/a.py:f")
        by_file_symbol = query.code_touches(index, "README.md", "README.md")
    finally:
        index.close()

    assert [row["item"] for row in by_path] == ["t1", "t0"]
    assert [row["record"] for row in by_path] == [
        "01JRUN000000000000000000A2",
        "01JRUN000000000000000000A1",
    ]
    assert by_path[0]["at"] == "2026-10-28T11:00:00Z"
    assert [row["item"] for row in by_symbol] == ["t0"]
    assert by_symbol[0]["symbol"] == "src/a.py:f"
    assert by_file_symbol == []


def test_a_run_without_code_keys_adds_no_rows(tmp_path: Path) -> None:
    store = make_store(tmp_path, 5)
    write_run(store, "01JRUN000000000000000000B1", item="t0")
    index = _synced(store)
    try:
        count = index.conn.execute("SELECT count(*) FROM code_touch").fetchone()[0]
    finally:
        index.close()

    assert count == 0


def test_code_touch_rebuilds_from_records(tmp_path: Path) -> None:
    store = _store_with_runs(tmp_path)
    index = _synced(store)
    before = query.code_touches(index, "src/a.py")
    index.close()
    index_path(store).unlink()

    index = _synced(store)
    try:
        after = query.code_touches(index, "src/a.py")
    finally:
        index.close()

    assert after == before and len(after) == 2


def test_an_old_index_schema_rebuilds(tmp_path: Path) -> None:
    store = _store_with_runs(tmp_path)
    index = _synced(store)
    index.close()
    conn = sqlite3.connect(index_path(store))
    conn.execute("DROP TABLE code_touch")
    conn.execute(f"PRAGMA user_version = {INDEX_SCHEMA - 1}")
    conn.commit()
    conn.close()

    index = _synced(store)
    try:
        rows = query.code_touches(index, "src/a.py")
    finally:
        index.close()

    assert len(rows) == 2
