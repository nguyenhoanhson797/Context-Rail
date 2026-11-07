"""Index schema 2: typed CTO projections kept in step with store documents."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.doc import record_digest
from context_rail.store.index import INDEX_SCHEMA, Index
from context_rail.store.location import index_path
from context_rail.store.query import execution_count, execution_latest, ledger, untriaged_reports
from helpers import (
    init_pack_store,
    run_cli,
    run_git,
    workflow_pack_document,
    write_pack,
    write_record,
)
from perf.gen import make_store

REQUIRED_TABLES = {
    "checks",
    "verdicts",
    "runs",
    "run_skills",
    "run_evaluations",
    "run_attempts",
    "code_touch",
    "reports",
    "triaged_reports",
    "approvals",
    "reviews",
    "executions",
}
BASE_TABLES = {"docs", "fields", "relations", "tags", "docs_fts", "meta"}
TRIAGE_ID = "01JTRIAGE00000000000000001"


@contextlib.contextmanager
def opened(store: Path, name: str = "basic") -> Iterator[Index]:
    index = Index.open(store, load_pack(name, store))
    try:
        yield index
    finally:
        index.close()


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        name
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def scalar(ix: Index, sql: str, params: tuple[object, ...] = ()) -> object:
    return ix.conn.execute(sql, params).fetchone()[0]


def rewrite_record(path: Path, change) -> None:
    """Apply ``change`` to a JSON Record payload and re-seal its digest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    payload["digest"] = record_digest(payload)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )


def record_file(store: Path, record_id: str) -> Path:
    return next((store / "records").rglob(f"{record_id}.json"))


def truth_files(store: Path) -> dict[Path, bytes]:
    """Every store file except the local index cache, which is never truth."""
    return {
        path: path.read_bytes()
        for path in store.rglob("*")
        if path.is_file() and ".rail-local" not in path.relative_to(store).parts
    }


def test_index_schema_six_keeps_the_base_tables_and_fts():
    assert INDEX_SCHEMA == 6


def test_open_creates_the_typed_tables(store_factory):
    store = store_factory(docs=5)
    with opened(store) as index:
        names = table_names(index.conn)
        assert REQUIRED_TABLES <= names
        assert BASE_TABLES <= names
        assert (
            index.conn.execute(
                "SELECT value FROM meta WHERE key = 'schema'"
            ).fetchone()[0]
            == "6"
        )
        assert index.conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_index_schema_bump_rebuilds_without_touching_truth(tmp_path):
    store = make_store(tmp_path, 20)
    before = truth_files(store)
    ix = Index.open(store, load_pack("basic", store))
    ix.sync()
    ix.close()
    conn = sqlite3.connect(index_path(store))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    ix = Index.open(store, load_pack("basic", store))
    try:
        assert ix.sync().added == 20
        assert REQUIRED_TABLES <= table_names(ix.conn)
    finally:
        ix.close()
    assert truth_files(store) == before


def test_a_document_row_carries_its_preset_workflow(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]

    with opened(store, "twoways") as ix:
        ix.sync()
        rows = ix.conn.execute(
            "SELECT slug, status, category, workflow FROM docs "
            "WHERE primitive = 'item' ORDER BY slug"
        ).fetchall()

    assert rows == [
        ("a-bug", "todo", "not_started", "bugfix"),
        ("a-task", "todo", "not_started", "sprint"),
    ]


def test_a_pack_change_reprojects_the_workflow(tmp_path: Path) -> None:
    document = workflow_pack_document()
    store = init_pack_store(tmp_path, document)
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]

    with opened(store, "twoways") as ix:
        ix.sync()
        workflow = ix.conn.execute(
            "SELECT workflow FROM docs WHERE slug = 'a-task'"
        ).fetchone()[0]
    assert workflow == "sprint"

    document["presets"]["task"]["workflow"] = "bugfix"
    write_pack(store, "twoways", document)

    with opened(store, "twoways") as ix:
        ix.sync()
        workflow = ix.conn.execute(
            "SELECT workflow FROM docs WHERE slug = 'a-task'"
        ).fetchone()[0]
    assert workflow == "bugfix"


def test_a_moved_item_records_its_workflow(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]
    shown = invoke("show", {"slug": "a-bug"}, store=store)
    assert shown["ok"], shown
    etag = shown["value"]["doc"]["etag"]

    moved = invoke(
        "move",
        {"slug": "a-bug", "status": "fixing", "expect": etag},
        store=store,
        actor="cto",
    )

    assert moved["ok"], moved
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (store / "records").rglob("*.json")
    ]
    changed = [row for row in records if row.get("kind") == "status.changed"]
    assert changed[-1]["data"]["workflow"] == "bugfix"


def test_a_cache_built_before_a_seek_index_gains_it_on_open(tmp_path):
    """The human-wait seek is additive, so an older cache keeps its rows."""
    store = make_store(tmp_path, 20)
    ix = Index.open(store, load_pack("basic", store))
    ix.sync()
    ix.close()
    conn = sqlite3.connect(index_path(store))
    conn.execute("DROP INDEX reports_class_at")
    conn.commit()
    conn.close()

    ix = Index.open(store, load_pack("basic", store))
    try:
        assert scalar(ix, "SELECT count(*) FROM docs") == 20
        found = {
            row[0]
            for row in ix.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'reports_class_at'"
            )
        }
        assert found == {"reports_class_at"}
    finally:
        ix.close()


def test_corrupt_database_is_rebuilt_with_the_typed_tables(store_factory):
    store = store_factory(docs=20)
    path = index_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"garbage" * 16)
    before = truth_files(store)
    with opened(store) as index:
        assert index.sync().added == 20
        assert REQUIRED_TABLES <= table_names(index.conn)
        assert index.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert truth_files(store) == before


def test_verdict_and_run_projection_roundtrip(seeded_runs_store):
    ix = seeded_runs_store.ix
    row = ledger(ix, item="login-page")["runs"][0]
    assert row["attempt_count"] == 2
    assert row["candidate_commit"] == seeded_runs_store.candidate
    assert row["accepted"] is True
    ix.rebuild()
    assert ledger(ix, item="login-page")["runs"] == [row]


def test_usage_is_not_multiplied_by_joins(seeded_runs_store):
    totals = ledger(seeded_runs_store.ix, item="login-page")["totals"]
    assert totals["known_sum"]["output_tokens"] == 4


def test_projection_rows_are_replaced_not_duplicated(seeded_runs_store):
    ix = seeded_runs_store.ix
    path = record_file(seeded_runs_store.store, seeded_runs_store.verdict)

    def fail(payload: dict) -> None:
        payload["data"]["passed"] = False
        payload["data"]["reason"] = "the suite regressed"

    rewrite_record(path, fail)
    ix.sync()
    assert scalar(ix, "SELECT count(*) FROM verdicts") == 1
    assert scalar(ix, "SELECT passed FROM verdicts") == 0
    assert (
        scalar(
            ix,
            "SELECT count(*) FROM docs WHERE path LIKE 'records/%' AND broken = 1",
        )
        == 0
    )


def test_latest_evaluation_decides_the_run(seeded_runs_store):
    ix = seeded_runs_store.ix
    second = "b" * 40
    write_record(
        seeded_runs_store.store,
        "01JEVALUATION00000000002",
        "run.evaluated",
        {
            "schema": 1,
            "run": seeded_runs_store.run,
            "candidate_commit": second,
            "accepted": False,
            "check_verdicts": [],
            "review": None,
            "at": "2026-10-28T10:30:00Z",
        },
        at="2026-10-28T10:30:00Z",
        about=(seeded_runs_store.item,),
    )
    ix.sync()
    row = ledger(ix, item="login-page")["runs"][0]
    assert row["candidate_commit"] == second
    assert row["accepted"] is False


def test_removing_an_evaluation_falls_back_to_the_run(seeded_runs_store):
    ix = seeded_runs_store.ix
    record_file(seeded_runs_store.store, seeded_runs_store.evaluation).unlink()
    ix.sync()
    row = ledger(ix, item="login-page")["runs"][0]
    assert row["candidate_commit"] is None
    assert row["accepted"] is None
    assert scalar(ix, "SELECT count(*) FROM run_evaluations") == 0


def test_removed_resources_drop_their_child_rows(seeded_runs_store):
    ix = seeded_runs_store.ix
    assert scalar(ix, "SELECT count(*) FROM run_skills") == 1
    assert scalar(ix, "SELECT count(*) FROM run_attempts") == 2
    record_file(seeded_runs_store.store, seeded_runs_store.run).unlink()
    ix.sync()
    assert scalar(ix, "SELECT count(*) FROM runs") == 0
    assert scalar(ix, "SELECT count(*) FROM run_skills") == 0
    assert scalar(ix, "SELECT count(*) FROM run_attempts") == 0


def test_malformed_typed_payload_is_a_problem_without_proof(seeded_runs_store):
    ix = seeded_runs_store.ix
    path = record_file(seeded_runs_store.store, seeded_runs_store.run)

    def corrupt(payload: dict) -> None:
        payload["data"]["not_a_run_key"] = 1

    rewrite_record(path, corrupt)
    ix.sync()
    assert scalar(ix, "SELECT count(*) FROM runs") == 0
    assert scalar(ix, "SELECT count(*) FROM run_attempts") == 0
    problems, broken = ix.conn.execute(
        "SELECT problems, broken FROM docs WHERE path LIKE 'records/%' "
        "AND slug = ?",
        (seeded_runs_store.run,),
    ).fetchone()
    assert "invalid_payload" in json.loads(problems)
    assert broken == 0


@pytest.mark.parametrize("kind", [["event"], {"name": "event"}])
def test_a_non_text_record_kind_records_an_invalid_payload(
    seeded_runs_store, kind
) -> None:
    ix = seeded_runs_store.ix
    path = record_file(seeded_runs_store.store, seeded_runs_store.run)

    def corrupt(payload: dict) -> None:
        payload["kind"] = kind

    rewrite_record(path, corrupt)
    ix.sync()

    problems, broken = ix.conn.execute(
        "SELECT problems, broken FROM docs WHERE path LIKE 'records/%' AND slug = ?",
        (seeded_runs_store.run,),
    ).fetchone()
    assert "invalid_payload" in json.loads(problems)
    assert broken == 0
    assert scalar(ix, "SELECT count(*) FROM checks") == 1
    assert scalar(ix, "SELECT count(*) FROM verdicts") == 1


def test_malformed_check_contributes_no_check_row(seeded_runs_store):
    ix = seeded_runs_store.ix
    path = seeded_runs_store.store / "checks" / "broken.md"
    path.write_text(
        "---\nprimitive: check\nschema: 1\nmode: widget\nacceptance: true\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    ix.sync()
    assert scalar(ix, "SELECT count(*) FROM checks WHERE slug = 'broken'") == 0
    assert scalar(ix, "SELECT count(*) FROM checks") == 1


def test_triage_record_links_its_reports(seeded_runs_store):
    ix = seeded_runs_store.ix
    assert [row["report"] for row in untriaged_reports(ix)] == [
        seeded_runs_store.report
    ]
    write_record(
        seeded_runs_store.store,
        TRIAGE_ID,
        "report.triaged",
        {
            "schema": 1,
            "reports": [seeded_runs_store.report],
            "cluster": "engine|parser|every token is kept|one token is lost",
            "weight": 2,
            "action": "fix",
            "outputs": ["fix-parser"],
        },
        at="2026-10-28T10:40:00Z",
        about=(),
    )
    ix.sync()
    assert untriaged_reports(ix) == []
    assert scalar(ix, "SELECT count(*) FROM triaged_reports") == 1


def test_unknown_projection_evidence_is_not_granted(seeded_runs_store):
    """A report whose payload is valid but whose parent run is missing still projects."""
    ix = seeded_runs_store.ix
    store = seeded_runs_store.store
    record_file(store, seeded_runs_store.run).unlink()
    ix.sync()
    reports = untriaged_reports(ix)
    assert [row["report"] for row in reports] == [seeded_runs_store.report]
    assert scalar(ix, "SELECT count(*) FROM runs") == 0


@pytest.mark.parametrize("kind", ["approval.recorded", "review.verdict"])
def test_unknown_keys_in_later_payloads_are_refused(seeded_runs_store, kind):
    ix = seeded_runs_store.ix
    table = "approvals" if kind == "approval.recorded" else "reviews"
    assert scalar(ix, f"SELECT count(*) FROM {table}") == 1
    record_id = (
        seeded_runs_store.approval if kind == "approval.recorded" else seeded_runs_store.review
    )

    def corrupt(payload: dict) -> None:
        payload["data"]["smuggled"] = True

    rewrite_record(record_file(seeded_runs_store.store, record_id), corrupt)
    ix.sync()
    assert scalar(ix, f"SELECT count(*) FROM {table}") == 0


def test_cli_status_and_reindex_rebuild_a_corrupt_index(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init")
    init = run_cli(root, "--actor", "human", "init", "--dir", ".", "--scaffold")
    assert init.returncode == 0, init.stdout
    added = run_cli(root, "--actor", "cto", "add", "task", "A")
    assert added.returncode == 0, added.stdout
    store = root / "context-rail"
    before = {
        path.relative_to(store).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.rglob("*")
        if path.is_file()
    }
    found = list((root / ".git").rglob("index.sqlite3"))
    assert len(found) == 1
    found[0].write_bytes(b"garbage" * 13)
    for args in (("status",), ("reindex",)):
        proc = run_cli(root, *args)
        assert proc.returncode == 0, (args, proc.stdout)
        assert json.loads(proc.stdout)["ok"] is True
    conn = sqlite3.connect(index_path(store))
    try:
        assert REQUIRED_TABLES <= table_names(conn)
    finally:
        conn.close()
    after = {
        path.relative_to(store).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_execution_records_keep_attempts_and_the_latest_state(store_factory) -> None:
    store = store_factory(docs=3)
    for attempt, state in ((1, "failed"), (2, "succeeded")):
        write_record(
            store,
            f"01JEXECUTION000000000000{attempt}",
            "execution.completed",
            {
                "schema": 1,
                "action": "render",
                "target": "shot-1",
                "state": state,
                "attempt": attempt,
                "started_at": f"2026-11-04T10:0{attempt}:00Z",
                "completed_at": f"2026-11-04T10:0{attempt}:30Z",
                "outputs": [f"renders/shot-1-{attempt}.mp4"],
                "error": None if state == "succeeded" else "encoder stopped",
                "actor": "renderer",
                "tool": "runner",
                "params": {"quality": "hero"},
                "runner_digest": "sha256:runner",
            },
            at=f"2026-11-04T10:0{attempt}:30Z",
            about=("shot-1",),
        )
    with opened(store) as index:
        index.sync()
        latest = execution_latest(index, actions=("render",), targets=("shot-1",))
        assert execution_count(index, "render", "shot-1") == 2
        assert len(latest) == 1
        assert latest[0]["state"] == "succeeded"
        assert latest[0]["attempt"] == 2
        assert latest[0]["outputs"] == ["renders/shot-1-2.mp4"]
        assert latest[0]["params"] == {"quality": "hero"}
