"""The per-worktree SQLite index: schema, incremental sync and rebuild."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pytest

from context_rail.model.pack import Pack, load_pack
from context_rail.store.doc import record_digest
from context_rail.store.index import INDEX_SCHEMA, Index, IndexBusy, SyncStats
from context_rail.store.location import index_path
from context_rail.store.query import find
from helpers import touch_doc

_SRC = Path(__file__).resolve().parents[2] / "src"

TASK_DOC = b"""---
primitive: item
preset: task
title: Extra
status: todo
tags: [task]
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
---
# Extra

An extra task body that is long enough to search.
"""

COLD_CHILD = """
import sys
import time
from pathlib import Path

from context_rail.model.pack import load_pack
from context_rail.store.index import Index

store = Path(sys.argv[1])
start = Path(sys.argv[2])
pack = load_pack("small", store)
deadline = time.monotonic() + 60
while not start.exists():
    if time.monotonic() > deadline:
        raise SystemExit("start file never appeared")
    time.sleep(0.005)
index = Index.open(store, pack)
index.sync()
index.close()
"""


@contextlib.contextmanager
def opened(
    store: Path, pack: Pack | None = None, **kwargs: object
) -> Iterator[Index]:
    index = Index.open(store, pack or load_pack("small", store), **kwargs)
    try:
        yield index
    finally:
        index.close()


OUTSIDE_DOC = b"""---
primitive: item
preset: task
title: Escaped
status: todo
tags: [task]
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
---
# Escaped

A valid document outside the store that must stay unindexed: zonk.
"""

CYCLE_CHILD = """
import json
import sys
from pathlib import Path

from context_rail.model.pack import load_pack
from context_rail.store.index import Index

store = Path(sys.argv[1])
index = Index.open(store, load_pack("small", store))
stats = index.sync()
paths = sorted(row[0] for row in index.conn.execute("SELECT path FROM docs"))
print(json.dumps({"added": stats.added, "skipped": stats.skipped, "paths": paths}))
index.close()
"""


def count(index: Index, sql: str, params: tuple[object, ...] = ()) -> int:
    assert index.conn is not None
    return index.conn.execute(sql, params).fetchone()[0]


def outside_store(root: Path) -> Path:
    """A folder outside the store holding one valid, searchable document."""
    outside = Path(root) / "outside"
    (outside / "items" / "task").mkdir(parents=True)
    (outside / "items" / "task" / "escaped.md").write_bytes(OUTSIDE_DOC)
    return outside


def junction(link: Path, target: Path) -> None:
    """Create a real Windows directory junction, skipping where unsupported."""
    if os.name != "nt":
        pytest.skip("directory junctions are a Windows feature")
    link.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"cannot create a junction: {(proc.stderr or proc.stdout).strip()}")


def symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    """Create a symlink, skipping on machines without the privilege."""
    try:
        os.symlink(target, link, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink: {exc}")


def drop(link: Path) -> None:
    """Remove a link itself, never its target."""
    with contextlib.suppress(OSError):
        os.rmdir(link)
    with contextlib.suppress(OSError):
        os.unlink(link)


def test_open_creates_a_wal_database_without_parsing_documents(store_factory):
    store = store_factory(docs=50)
    index = Index.open(store, load_pack("small", store))
    try:
        assert INDEX_SCHEMA == 2
        assert index_path(store).is_file()
        assert index.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert count(index, "SELECT count(*) FROM docs") == 0
        assert index.sync() == SyncStats(50, 0, 0, 0)
        assert count(index, "SELECT count(*) FROM docs") == 50
    finally:
        index.close()


def test_sync_is_incremental(store_factory):
    store = store_factory(docs=50)
    with opened(store) as index:
        assert index.sync().added == 50
        assert index.sync() == SyncStats(0, 0, 0, 0)
        touch_doc(store, "items/task/t1.md", title="changed")
        stats = index.sync()
        assert (stats.added, stats.changed, stats.removed) == (0, 1, 0)
        assert (
            index.conn.execute(
                "SELECT title FROM docs WHERE path = ?", ("items/task/t1.md",)
            ).fetchone()[0]
            == "changed"
        )


def test_removed_file_disappears(store_factory):
    store = store_factory(docs=20)
    note = sorted((store / "notes" / "note").glob("n*.md"))[0]
    rel = note.relative_to(store).as_posix()
    with opened(store) as index:
        assert index.sync().added == 20
        note.unlink()
        assert index.sync() == SyncStats(0, 0, 1, 0)
        assert count(index, "SELECT count(*) FROM docs WHERE path = ?", (rel,)) == 0
        assert count(index, "SELECT count(*) FROM fields WHERE path = ?", (rel,)) == 0
        assert count(index, "SELECT count(*) FROM tags WHERE path = ?", (rel,)) == 0
        assert count(index, "SELECT count(*) FROM docs_fts WHERE path = ?", (rel,)) == 0


def test_unchanged_stat_rows_are_not_reparsed(store_factory):
    store = store_factory(docs=20)
    path = store / "items" / "task" / "t1.md"
    with opened(store) as index:
        assert index.sync().added == 20
        before = index.conn.execute(
            "SELECT title FROM docs WHERE path = ?", ("items/task/t1.md",)
        ).fetchone()[0]
        stat = path.stat()
        edited = path.read_bytes().replace(b"Task 1", b"Task Z", 1)
        path.write_bytes(edited)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert path.stat().st_size == stat.st_size
        assert path.stat().st_mtime_ns == stat.st_mtime_ns
        assert index.sync() == SyncStats(0, 0, 0, 0)
        assert (
            index.conn.execute(
                "SELECT title FROM docs WHERE path = ?", ("items/task/t1.md",)
            ).fetchone()[0]
            == before
        )
        touch_doc(store, "items/task/t1.md", title="again")
        assert index.sync().changed == 1
        assert (
            index.conn.execute(
                "SELECT title FROM docs WHERE path = ?", ("items/task/t1.md",)
            ).fetchone()[0]
            == "again"
        )


def test_corrupt_db_is_rebuilt(store_factory):
    store = store_factory(docs=20)
    path = index_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a sqlite database")
    index = Index.open(store, load_pack("small", store))
    try:
        assert index.sync().added == 20
        assert index.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        index.close()
    path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    assert not path.exists()


def test_pack_change_rebuilds_the_index(store_factory):
    store = store_factory(docs=20)
    with opened(store) as index:
        assert index.sync().added == 20
    with opened(store) as index:
        assert index.sync() == SyncStats(0, 0, 0, 0)

    source = resources.files("context_rail").joinpath("packs/small/pack.yaml")
    changed = source.read_text(encoding="utf-8").replace("initial: todo", "initial: done")
    assert "initial: done" in changed
    local = store / "packs" / "small" / "pack.yaml"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(changed, encoding="utf-8")

    with opened(store) as index:
        assert index.sync().added == 20


def test_simultaneous_cold_openers(store_factory, tmp_path):
    store = store_factory(docs=30)
    start = tmp_path / "start"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", COLD_CHILD, str(store), str(start)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(4)
    ]
    start.write_text("go", encoding="utf-8")
    try:
        outputs = [child.communicate(timeout=120) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    for child, (_, err) in zip(children, outputs):
        assert child.returncode == 0, err
        assert "Traceback" not in err

    with opened(store) as index:
        assert count(index, "SELECT count(*) FROM docs") == 30
        assert count(index, "SELECT count(*) FROM docs_fts") == 30
        assert index.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert index.sync() == SyncStats(0, 0, 0, 0)


def test_busy_write_is_infrastructure_not_corruption(store_factory):
    store = store_factory(docs=20)
    path = index_path(store)
    with opened(store, busy_timeout_ms=50) as index:
        assert index.sync().added == 20
        assert index.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        identity = (path.stat().st_ino, path.stat().st_size)
        touch_doc(store, "items/task/t0.md", title="busy")
        truth = (store / "items" / "task" / "t0.md").read_bytes()

        blocker = sqlite3.connect(str(path), isolation_level=None)
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            with pytest.raises(IndexBusy):
                index.sync()
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

        assert path.exists()
        assert (path.stat().st_ino, path.stat().st_size) == identity
        assert count(index, "SELECT count(*) FROM docs") == 20
        assert index.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (store / "items" / "task" / "t0.md").read_bytes() == truth
        assert index.sync() == SyncStats(0, 1, 0, 0)


def test_bad_yaml_doc_does_not_block_others(store_factory):
    store = store_factory(docs=20)
    (store / "items" / "task" / "broken.md").write_bytes(b"---\ntitle: [unclosed\n---\n")
    with opened(store) as index:
        assert index.sync().added == 21
        problems = index.conn.execute(
            "SELECT problems FROM docs WHERE path = ?", ("items/task/broken.md",)
        ).fetchone()[0]
        assert "parse_error" in problems
        assert count(index, "SELECT count(*) FROM docs") == 21
        assert count(
            index,
            "SELECT count(*) FROM docs WHERE problems = '[]'",
        ) == 20


def test_binary_and_tmp_files_skipped(store_factory):
    store = store_factory(docs=10)
    (store / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (store / "items" / "task" / "login.md.123.tmp").write_bytes(TASK_DOC)
    with opened(store) as index:
        first = index.sync()
        assert (first.added, first.skipped) == (10, 1)
        second = index.sync()
        assert (second.added, second.skipped) == (0, 1)
        assert count(index, "SELECT count(*) FROM docs") == 10


def test_only_md_and_json_are_parsed_and_other_files_counted(store_factory):
    store = store_factory(docs=10)
    (store / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (store / "notes" / "readme.txt").write_text("read me", encoding="utf-8")
    (store / "items" / "task" / "login.md.123.tmp").write_bytes(TASK_DOC)
    (store / ".hidden").mkdir()
    (store / ".hidden" / "x.md").write_bytes(TASK_DOC)
    (store / "templates").mkdir()
    (store / "templates" / "item.md").write_bytes(TASK_DOC)
    (store / "packs" / "small" / "templates").mkdir(parents=True)
    (store / "packs" / "small" / "templates" / "item.md").write_bytes(TASK_DOC)
    with opened(store) as index:
        first = index.sync()
        assert (first.added, first.skipped) == (10, 2)
        second = index.sync()
        assert (second.added, second.skipped) == (0, 2)
        paths = {row[0] for row in index.conn.execute("SELECT path FROM docs")}
        assert len(paths) == 10
        assert "rail.yaml" not in paths
        for excluded in (
            "items/task/login.md.123.tmp",
            ".hidden/x.md",
            "templates/item.md",
            "packs/small/templates/item.md",
            "image.png",
            "notes/readme.txt",
        ):
            assert excluded not in paths


def test_problem_rows_keep_no_content_and_duplicate_stems_stay_separate(store_factory):
    store = store_factory(docs=20)
    (store / "items" / "task" / "broken.md").write_bytes(b"---\ntitle: [unclosed\n---\n")
    (store / "items" / "task" / "bad-bytes.md").write_bytes(b"---\ntitle: \xff\n---\n")
    with opened(store) as index:
        assert index.sync().added == 22
        problems = dict(
            index.conn.execute("SELECT path, problems FROM docs").fetchall()
        )
        assert "parse_error" in problems["items/task/broken.md"]
        assert "bad_encoding" in problems["items/task/bad-bytes.md"]
        for broken in ("items/task/broken.md", "items/task/bad-bytes.md"):
            assert count(index, "SELECT count(*) FROM fields WHERE path = ?", (broken,)) == 0
            assert (
                count(index, "SELECT count(*) FROM relations WHERE src_path = ?", (broken,))
                == 0
            )
            assert count(index, "SELECT count(*) FROM tags WHERE path = ?", (broken,)) == 0
            assert (
                count(index, "SELECT count(*) FROM docs_fts WHERE path = ?", (broken,))
                == 0
            )

        (store / "items" / "task" / "x.md").write_bytes(TASK_DOC)
        (store / "notes" / "note" / "x.md").write_bytes(TASK_DOC)
        assert index.sync().added == 2
        rows = index.conn.execute(
            "SELECT path FROM docs WHERE slug = 'x' ORDER BY path"
        ).fetchall()
        assert [row[0] for row in rows] == ["items/task/x.md", "notes/note/x.md"]


def test_records_carry_digest_and_activity_timestamp(store_factory):
    store = store_factory(docs=20)
    payload = {
        "id": "01J",
        "primitive": "record",
        "preset": "event",
        "kind": "doc.created",
        "at": "2026-10-28T00:00:00Z",
        "actor": "cto",
        "about": ["t0"],
        "data": {},
    }
    payload["digest"] = record_digest(payload)
    records = store / "records" / "2026-10"
    records.mkdir(parents=True)
    (records / "01J.json").write_text(json.dumps(payload), encoding="utf-8")
    (records / "01K.json").write_text(
        json.dumps({**payload, "id": "01K", "at": "not a timestamp"}), encoding="utf-8"
    )
    decision = store / "records" / "decisions" / "use-sqlite.md"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        "---\nprimitive: record\npreset: decision\ntitle: Use SQLite\n"
        "at: 2026-10-27T12:00:00Z\ndigest: abc123\n---\n# Use SQLite\n",
        encoding="utf-8",
    )

    def stamp(*args: int) -> int:
        return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)

    with opened(store) as index:
        assert index.sync().added == 23
        row = index.conn.execute(
            "SELECT digest, record_at FROM docs WHERE path = ?",
            ("records/2026-10/01J.json",),
        ).fetchone()
        assert row[0] == payload["digest"]
        assert row[1] == stamp(2026, 10, 28)
        invalid = index.conn.execute(
            "SELECT record_at FROM docs WHERE path = ?", ("records/2026-10/01K.json",)
        ).fetchone()[0]
        assert invalid is None
        markdown = index.conn.execute(
            "SELECT digest, record_at FROM docs WHERE path = ?",
            ("records/decisions/use-sqlite.md",),
        ).fetchone()
        assert markdown == ("abc123", stamp(2026, 10, 27, 12))


def test_duplicate_stems_both_indexed(store_factory):
    store = store_factory(docs=20)
    (store / "items" / "task" / "x.md").write_bytes(TASK_DOC)
    (store / "notes" / "note" / "x.md").write_bytes(TASK_DOC)
    with opened(store) as index:
        assert index.sync().added == 22
        rows = index.conn.execute(
            "SELECT path FROM docs WHERE slug = 'x' ORDER BY path"
        ).fetchall()
        assert [row[0] for row in rows] == ["items/task/x.md", "notes/note/x.md"]


def test_large_body_capped(store_factory):
    store = store_factory(docs=20)
    body = "body " * 400_000
    text = (
        "---\nprimitive: item\npreset: task\ntitle: Big\nstatus: todo\ntags: [task]\n"
        "---\n" + body
    )
    (store / "items" / "task" / "big.md").write_text(text, encoding="utf-8")
    with opened(store) as index:
        assert index.sync().added == 21
        assert (
            count(
                index,
                "SELECT length(excerpt) FROM docs WHERE path = ?",
                ("items/task/big.md",),
            )
            == 500
        )
        assert (
            count(index, "SELECT length(body) FROM docs_fts WHERE slug = 'big'")
            == 20000
        )


def test_index_lives_under_the_git_dir(git_repo_with_worktree):
    store, worktree_store = git_repo_with_worktree
    main_path = index_path(store)
    with opened(store):
        assert main_path.is_file()
    with opened(worktree_store):
        worktree_path = index_path(worktree_store)
        assert worktree_path.is_file()
    assert main_path.is_relative_to(store.parent / ".git")
    assert not main_path.is_relative_to(store)
    assert worktree_path != main_path


def test_junction_to_outside_directory_is_not_indexed(store_factory, tmp_path):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "linked"
    junction(link, outside_store(tmp_path))
    try:
        with opened(store) as index:
            stats = index.sync()
            assert (stats.added, stats.skipped) == (10, 0)
            paths = [row[0] for row in index.conn.execute("SELECT path FROM docs")]
            assert not any(path.startswith("items/task/linked/") for path in paths)
            assert (
                count(index, "SELECT count(*) FROM docs_fts WHERE body LIKE '%zonk%'")
                == 0
            )
            assert find(index, text="zonk")["items"] == []
    finally:
        drop(link)


def test_junction_cycle_sync_terminates(store_factory):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "loop"
    junction(link, store)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", CYCLE_CHILD, str(store)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    finally:
        drop(link)
    assert "Traceback" not in proc.stderr
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert (result["added"], result["skipped"]) == (10, 0)
    assert not [path for path in result["paths"] if "loop" in path]


def test_junction_to_inside_directory_is_still_skipped(store_factory):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "echo"
    junction(link, store / "notes")
    try:
        with opened(store) as index:
            stats = index.sync()
            assert (stats.added, stats.skipped) == (10, 0)
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path LIKE 'items/task/echo/%'",
                )
                == 0
            )
    finally:
        drop(link)


def test_junction_detection_works_without_python_312_helpers(
    store_factory, tmp_path, monkeypatch
):
    monkeypatch.delattr(os.path, "isjunction", raising=False)

    def unavailable(*args: object, **kwargs: object) -> bool:
        raise AssertionError("Path.is_junction is unavailable before Python 3.12")

    monkeypatch.setattr(Path, "is_junction", unavailable, raising=False)
    store = store_factory(docs=10)
    link = store / "items" / "task" / "linked"
    junction(link, outside_store(tmp_path))
    try:
        with opened(store) as index:
            assert index.sync().added == 10
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path LIKE 'items/task/linked/%'",
                )
                == 0
            )
    finally:
        drop(link)


def test_contained_file_symlink_is_indexed(store_factory):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "alias.md"
    symlink(link, store / "items" / "task" / "t0.md")
    try:
        with opened(store) as index:
            assert index.sync().added == 11
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path = 'items/task/alias.md'",
                )
                == 1
            )
    finally:
        drop(link)


def test_escaping_file_symlink_is_not_indexed(store_factory, tmp_path):
    outside = outside_store(tmp_path)
    store = store_factory(docs=10)
    link = store / "items" / "task" / "escaped.md"
    symlink(link, outside / "items" / "task" / "escaped.md")
    try:
        with opened(store) as index:
            assert index.sync().added == 10
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path = 'items/task/escaped.md'",
                )
                == 0
            )
            assert find(index, text="zonk")["items"] == []
    finally:
        drop(link)


def test_directory_symlink_to_outside_is_not_traversed(store_factory, tmp_path):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "linked"
    symlink(link, outside_store(tmp_path), directory=True)
    try:
        with opened(store) as index:
            stats = index.sync()
            assert (stats.added, stats.skipped) == (10, 0)
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path LIKE 'items/task/linked/%'",
                )
                == 0
            )
    finally:
        drop(link)


def test_contained_directory_symlink_is_not_traversed(store_factory):
    store = store_factory(docs=10)
    link = store / "items" / "task" / "alias"
    symlink(link, store / "notes", directory=True)
    try:
        with opened(store) as index:
            stats = index.sync()
            assert (stats.added, stats.skipped) == (10, 0)
            assert (
                count(
                    index,
                    "SELECT count(*) FROM docs WHERE path LIKE 'items/task/alias/%'",
                )
                == 0
            )
    finally:
        drop(link)
