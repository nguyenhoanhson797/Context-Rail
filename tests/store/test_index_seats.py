"""Seat records projected into the index: events, the live fold and rebuilds."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from context_rail.kernel.seats import LiveSeat
from context_rail.model.pack import load_pack
from context_rail.model.seats import SEAT_GRANTED, SEAT_REVOKED
from context_rail.store import query
from context_rail.store.doc import record_digest
from context_rail.store.index import INDEX_SCHEMA, Index
from context_rail.store.location import index_path
from helpers import init_store, write_record


@contextlib.contextmanager
def opened(store: Path) -> Iterator[Index]:
    index = Index.open(store, load_pack("basic", store))
    try:
        index.sync()
        yield index
    finally:
        index.close()


def grant(
    store: Path,
    record_id: str,
    seat: str,
    by: str,
    at: str,
    *,
    role: str = "worker",
    anchor: str = "project",
) -> Path:
    data = {"schema": 1, "seat": seat, "role": role, "anchor": anchor, "granted_by": by}
    return write_record(store, record_id, SEAT_GRANTED, data, at=at, actor=by)


def revoke(store: Path, record_id: str, seat: str, at: str) -> Path:
    data = {"schema": 1, "seat": seat, "revoked_by": "human"}
    return write_record(store, record_id, SEAT_REVOKED, data, at=at, actor="human")


def test_grant_and_revoke_records_are_projected(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    grant(store, "g1", "lead-1", "human", "2026-11-10T10:00:00Z", role="lead")
    revoke(store, "r1", "lead-1", "2026-11-10T11:00:00Z")

    with opened(store) as ix:
        events = query.seat_events(ix)

    assert [(e.kind, e.seat, e.by, e.record_id) for e in events] == [
        (SEAT_GRANTED, "lead-1", "human", "g1"),
        (SEAT_REVOKED, "lead-1", "human", "r1"),
    ]
    assert events[0].role == "lead" and events[0].anchor == "project"
    assert events[0].at == "2026-11-10T10:00:00Z"


def test_live_seats_apply_the_cascade(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    grant(store, "g1", "lead", "human", "2026-11-10T10:00:00Z", role="lead")
    grant(
        store,
        "g2",
        "manager",
        "lead",
        "2026-11-10T10:01:00Z",
        role="manager",
        anchor="orders",
    )
    grant(store, "g3", "other", "human", "2026-11-10T10:02:00Z")

    with opened(store) as ix:
        before = query.live_seats(ix)
    revoke(store, "r1", "lead", "2026-11-10T11:00:00Z")
    with opened(store) as ix:
        after = query.live_seats(ix)

    assert set(before) == {"lead", "manager", "other"}
    assert before["manager"] == LiveSeat(
        "manager", "manager", "orders", None, "lead", "2026-11-10T10:01:00Z"
    )
    assert set(after) == {"other"}


def test_a_malformed_seat_record_is_flagged_not_projected(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    path = write_record(
        store,
        "bad",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "Not A Slug",
            "role": "worker",
            "anchor": "project",
            "granted_by": "human",
        },
        at="2026-11-10T10:00:00Z",
    )

    with opened(store) as ix:
        assert query.live_seats(ix) == {}
        problems = ix.conn.execute(
            "SELECT problems FROM docs WHERE path LIKE ?", (f"%{path.name}",)
        ).fetchone()[0]

    assert "invalid_payload" in problems


def test_an_old_index_schema_rebuilds_with_the_seats_table(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    grant(store, "g1", "lead", "human", "2026-11-10T10:00:00Z", role="lead")
    with opened(store):
        pass
    conn = sqlite3.connect(index_path(store))
    conn.execute("DROP TABLE seats")
    conn.execute(f"PRAGMA user_version = {INDEX_SCHEMA - 1}")
    conn.commit()
    conn.close()

    with opened(store) as ix:
        assert set(query.live_seats(ix)) == {"lead"}


def test_removing_a_seat_record_removes_its_row(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    path = grant(store, "g1", "lead", "human", "2026-11-10T10:00:00Z", role="lead")
    with opened(store) as ix:
        assert set(query.live_seats(ix)) == {"lead"}
    path.unlink()

    with opened(store) as ix:
        assert query.live_seats(ix) == {}
        assert ix.conn.execute("SELECT count(*) FROM seats").fetchone()[0] == 0


def test_an_edited_seat_record_replaces_its_row(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    path = grant(store, "g1", "lead", "human", "2026-11-10T10:00:00Z", role="lead")
    with opened(store) as ix:
        assert query.live_seats(ix)["lead"].role == "lead"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    payload["data"]["role"] = "reviewer"
    payload["digest"] = record_digest(payload)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )

    with opened(store) as ix:
        assert query.live_seats(ix)["lead"].role == "reviewer"
        assert ix.conn.execute("SELECT count(*) FROM seats").fetchone()[0] == 1


def test_no_seat_records_means_no_live_seats(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    with opened(store) as ix:
        assert query.seat_events(ix) == []
        assert query.live_seats(ix) == {}
