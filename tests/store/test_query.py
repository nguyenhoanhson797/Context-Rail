"""The bounded query layer: find, show, records_about and last_activity_ms."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from context_rail.core.util import sha256_hex
from context_rail.model.pack import load_pack
from context_rail.store.doc import record_digest
from context_rail.store.index import Index
from context_rail.store.query import (
    QueryError,
    find,
    last_activity_ms,
    records_about,
    show,
)
from helpers import touch_doc

STAMP = "2026-10-28T00:00:00Z"

DUP_TASK = f"""---
primitive: item
preset: task
title: Dup
status: todo
tags: [task, task]
created: '{STAMP}'
updated: '{STAMP}'
part_of: '[[g1]]'
depends_on: [b, a, a]
---
# Dup

A duplicated task body.
"""

ZZ_NOTE = f"""---
primitive: note
preset: note
title: Zz
tags: []
created: '{STAMP}'
updated: '{STAMP}'
---
# Zz

The needle zorbtoken appears only in this note.
"""


@contextlib.contextmanager
def opened(store: Path) -> Iterator[Index]:
    index = Index.open(store, load_pack("small", store))
    index.sync()
    try:
        yield index
    finally:
        index.close()


def record_payload(
    ident: str, at: str, about: tuple[str, ...] = ("login",)
) -> dict:
    payload = {
        "id": ident,
        "primitive": "record",
        "preset": "event",
        "kind": "doc.created",
        "at": at,
        "actor": "human",
        "about": list(about),
        "data": {},
    }
    payload["digest"] = record_digest(payload)
    return payload


def write_item(store: Path, slug: str, *, status: str, updated: str) -> None:
    path = store / f"items/task/{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "primitive: item\n"
        "preset: task\n"
        f"title: {slug.title()}\n"
        f"status: {status}\n"
        f"created: '{updated}'\n"
        f"updated: '{updated}'\n"
        "tags: [task]\n"
        "---\n"
        f"# {slug.title()}\n\nA body for {slug}.\n",
        encoding="utf-8",
        newline="\n",
    )


def test_find_returns_pinned_shape_sorted_and_paged(indexed_store):
    result = find(indexed_store.ix, preset="task")

    assert set(result) == {"items", "total", "omitted"}
    assert result["total"] == 140
    assert len(result["items"]) == 50
    assert result["omitted"] == 90
    for item in result["items"]:
        assert set(item) == {"slug", "title", "primitive", "preset", "status", "path"}

    slugs = [item["slug"] for item in result["items"]]
    assert slugs[:4] == ["t0", "t1", "t10", "t100"]
    first = result["items"][0]
    assert first == {
        "slug": "t0",
        "title": "Task 0",
        "primitive": "item",
        "preset": "task",
        "status": "todo",
        "path": "items/task/t0.md",
    }

    touch_doc(indexed_store.store, "items/task/t120.md", updated="2026-11-01T00:00:00Z")
    indexed_store.ix.sync()
    assert find(indexed_store.ix, preset="task")["items"][0]["slug"] == "t120"


def test_find_filters_combine_and_count_each_doc_once(indexed_store):
    index = indexed_store.ix

    doing = find(index, status="doing")
    assert {item["status"] for item in doing["items"]} == {"doing"}
    assert find(index, category="active")["total"] == doing["total"]

    related = find(index, rel=("part_of", "g1"))
    assert related["total"] == 7
    assert [item["path"] for item in related["items"]] == [
        "items/task/t1.md",
        "items/task/t101.md",
        "items/task/t121.md",
        "items/task/t21.md",
        "items/task/t41.md",
        "items/task/t61.md",
        "items/task/t81.md",
    ]
    assert find(index, tags=("task",))["total"] == 140

    combined = find(index, preset="task", status="doing", rel=("part_of", "g1"))
    assert {item["slug"] for item in combined["items"]} == {"t1", "t61", "t121"}

    (indexed_store.store / "items/task/dup.md").write_text(
        DUP_TASK, encoding="utf-8", newline="\n"
    )
    index.sync()

    depends = find(index, rel=("depends_on", "a"))
    assert depends["total"] == 1
    assert [item["slug"] for item in depends["items"]] == ["dup"]
    assert find(index, tags=("task",))["total"] == 141
    assert show(index, "dup")["out"]["depends_on"] == ["a", "b"]


def test_find_text_uses_fts_and_binds_tag_values(indexed_store):
    store, index = indexed_store.store, indexed_store.ix
    note = store / "notes/note/zz.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(ZZ_NOTE, encoding="utf-8", newline="\n")
    index.sync()

    found = find(index, text="zorbtoken")
    assert found["total"] == 1
    assert [item["slug"] for item in found["items"]] == ["zz"]

    injected = find(index, tags=("x' OR '1'='1",))
    assert injected == {"items": [], "total": 0, "omitted": 0}
    assert find(index, preset="task")["total"] == 140


def test_duplicate_slug_text_search_matches_by_path(indexed_store_with_duplicate):
    index = indexed_store_with_duplicate.ix

    task = find(index, text="dupxtask")
    assert task["total"] == 1
    assert [(item["slug"], item["path"]) for item in task["items"]] == [
        ("x", "items/task/x.md")
    ]

    note = find(index, text="dupxnote")
    assert note["total"] == 1
    assert [(item["slug"], item["path"]) for item in note["items"]] == [
        ("x", "notes/note/x.md")
    ]

    both = find(index, text="dupxtask OR dupxnote")
    assert both["total"] == 2
    assert sorted(item["path"] for item in both["items"]) == [
        "items/task/x.md",
        "notes/note/x.md",
    ]


@pytest.mark.parametrize("limit", [0, 101, True, -1])
def test_find_refuses_invalid_limits(indexed_store, limit):
    with pytest.raises(QueryError) as caught:
        find(indexed_store.ix, limit=limit)
    assert caught.value.code == "VALIDATION_FAILED"


def test_find_refuses_malformed_match_syntax(indexed_store):
    with pytest.raises(QueryError) as caught:
        find(indexed_store.ix, text='"unclosed')
    assert caught.value.code == "VALIDATION_FAILED"


def test_find_accepts_limit_boundaries(indexed_store):
    assert len(find(indexed_store.ix, limit=1)["items"]) == 1
    assert len(find(indexed_store.ix, limit=100)["items"]) == 100


def test_show_returns_frontmatter_relations_and_etag(indexed_store):
    index = indexed_store.ix
    result = show(index, "t5")

    assert set(result) == {"doc", "out", "in", "body", "body_omitted"}
    doc = result["doc"]
    assert doc["slug"] == "t5"
    assert doc["path"] == "items/task/t5.md"
    assert doc["primitive"] == "item"
    assert doc["preset"] == "task"
    assert doc["title"] == "Task 5"
    assert doc["status"] == "done"
    assert doc["tags"] == ["task"]
    assert doc["created"] == STAMP
    assert doc["updated"] == STAMP
    data = (indexed_store.store / "items/task/t5.md").read_bytes()
    assert doc["etag"] == sha256_hex(data)[:16]

    assert result["out"] == {"part_of": ["g5"], "depends_on": ["t4"]}
    assert show(index, "g5")["in"]["part_of"] == [
        "t105",
        "t125",
        "t25",
        "t45",
        "t5",
        "t65",
        "t85",
    ]


def test_show_body_is_bounded_and_reports_omitted(indexed_store):
    index = indexed_store.ix
    full = show(index, "t0", body_chars=20000)["body"]
    assert full.startswith("# Task 0")

    capped = show(index, "t0", body_chars=100)
    assert len(capped["body"]) == 100
    assert capped["body_omitted"] == len(full) - 100

    empty = show(index, "t0", body_chars=0)
    assert empty["body"] == ""
    assert empty["body_omitted"] == len(full)

    unbounded = show(index, "t0", body_chars=20000)
    assert unbounded["body_omitted"] == 0


@pytest.mark.parametrize("body_chars", [-1, 20001, False])
def test_show_refuses_invalid_body_chars(indexed_store, body_chars):
    with pytest.raises(QueryError) as caught:
        show(indexed_store.ix, "t0", body_chars=body_chars)
    assert caught.value.code == "VALIDATION_FAILED"


def test_show_refuses_missing_and_ambiguous_slugs(
    indexed_store, indexed_store_with_duplicate
):
    with pytest.raises(LookupError) as missing:
        show(indexed_store.ix, "no-such-slug")
    assert str(missing.value) == "not_found"

    with pytest.raises(LookupError) as ambiguous:
        show(indexed_store_with_duplicate.ix, "x")
    assert str(ambiguous.value) == "ambiguous"


def test_records_about_returns_json_and_markdown_newest_first(store_factory):
    store = store_factory()
    write_item(store, "login", status="todo", updated=STAMP)

    records = store / "records/2026-10"
    records.mkdir(parents=True)
    for ident, at in (
        ("01A", "2026-10-28T00:00:00Z"),
        ("01B", "2026-10-29T00:00:00Z"),
        ("01C", "2026-10-29T00:00:00Z"),
    ):
        (records / f"{ident}.json").write_text(
            json.dumps(record_payload(ident, at)), encoding="utf-8", newline="\n"
        )
    decision = store / "records/decisions/use-sqlite.md"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        "---\n"
        "primitive: record\n"
        "preset: decision\n"
        "title: Use SQLite\n"
        "id: 01D\n"
        "at: '2030-10-30T00:00:00Z'\n"
        "kind: decision.made\n"
        "actor: human\n"
        "about: '[[login]]'\n"
        "---\n"
        "# Use SQLite\n",
        encoding="utf-8",
        newline="\n",
    )

    with opened(store) as index:
        found = records_about(index, "login")

    assert [payload["id"] for payload in found] == ["01D", "01C", "01B", "01A"]
    for payload in found[1:]:
        assert set(payload) == {
            "id",
            "primitive",
            "preset",
            "kind",
            "at",
            "actor",
            "about",
            "data",
            "digest",
        }
        assert payload["primitive"] == "record"
        assert payload["preset"] == "event"
        assert payload["about"] == ["login"]
        stored = json.loads((records / f"{payload['id']}.json").read_text("utf-8"))
        assert payload["digest"] == stored["digest"]
    assert found[0]["kind"] == "decision.made"
    assert found[0]["at"] == "2030-10-30T00:00:00Z"
    assert found[0]["data"] == {}


def test_last_activity_ms_counts_only_valid_record_at(store_factory):
    store = store_factory()
    write_item(store, "a", status="todo", updated="2030-01-01T00:00:00Z")
    write_item(store, "b", status="doing", updated=STAMP)
    write_item(store, "c", status="doing", updated=STAMP)
    write_item(store, "d", status="doing", updated=STAMP)

    records = store / "records/2026-10"
    records.mkdir(parents=True)
    entries = (
        ("01B1", "2026-10-28T00:00:00Z", ("b",)),
        ("01B2", "2026-10-29T00:00:00Z", ("b",)),
        ("01C1", "not-a-date", ("c",)),
        ("01D1", "2026-10-28T00:00:00Z", ("old-d",)),
    )
    for ident, at, about in entries:
        (records / f"{ident}.json").write_text(
            json.dumps(record_payload(ident, at, about)), encoding="utf-8", newline="\n"
        )

    with opened(store) as index:
        activity = last_activity_ms(index)

    expected = int(datetime(2026, 10, 29, tzinfo=timezone.utc).timestamp() * 1000)
    assert activity == {"b": expected}


def test_broken_documents_are_excluded_from_find_and_text(store_factory):
    store = store_factory(20)
    (store / "items/task/broken.md").write_text(
        "---\ntitle: [unclosed\n---\nqqbrokentoken", encoding="utf-8", newline="\n"
    )
    (store / "items/task/bad-bytes.md").write_bytes(
        b"---\ntitle: \xff\n---\nqqbrokentoken"
    )

    with opened(store) as index:
        everything = find(index, limit=100)
        broken = find(index, text="qqbrokentoken")

    assert everything["total"] == 20
    assert "items/task/broken.md" not in {item["path"] for item in everything["items"]}
    assert "items/task/bad-bytes.md" not in {
        item["path"] for item in everything["items"]
    }
    assert broken["total"] == 0


def test_reads_launch_no_processes_and_show_opens_only_its_doc(indexed_store):
    store = indexed_store.store.resolve()
    index = indexed_store.ix
    events: list[tuple[str, tuple]] = []
    recording = True

    def hook(event: str, args: tuple) -> None:
        if recording and event in ("subprocess.Popen", "os.system", "open"):
            events.append((event, args))

    sys.addaudithook(hook)
    opened_files: dict[str, list[str]] = {}
    processes: list[str] = []
    try:
        for name, call in (
            ("find", lambda: find(index, preset="task")),
            ("show", lambda: show(index, "t1")),
            ("records_about", lambda: records_about(index, "t1")),
            ("last_activity", lambda: last_activity_ms(index)),
            ("sync", lambda: index.sync()),
        ):
            events.clear()
            call()
            opened_files[name] = [
                _text(args[0])
                for event, args in events
                if event == "open"
                and args
                and isinstance(args[0], (str, bytes))
                and Path(_text(args[0])).is_relative_to(store)
                and _text(args[0]).lower().endswith((".md", ".json"))
            ]
            processes.extend(event for event, _ in events if event != "open")
    finally:
        recording = False

    assert processes == []
    assert opened_files["find"] == []
    assert opened_files["records_about"] == []
    assert opened_files["last_activity"] == []
    assert opened_files["sync"] == []
    assert opened_files["show"] == [str(store / "items/task/t1.md")]


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def test_one_record_data_reader_is_shared() -> None:
    from context_rail.gateway.handlers import measure as measure_handler
    from context_rail.store import measure as measure_store
    from context_rail.store import query, versions

    assert callable(query.record_data)
    for module in (measure_store, versions, measure_handler):
        assert not hasattr(module, "_record_data"), module


def test_a_reversible_decision_is_found_whatever_the_json_spacing(tmp_path) -> None:
    from context_rail.store import query
    from helpers import init_store, write_record

    store = init_store(tmp_path)
    write_record(
        store,
        "01JSPACEDDECISION000000001",
        "decision.made",
        {
            "schema": 1,
            "decision_class": "cto-reversible",
            "reason": "a spacing test",
            "before": {},
            "after": {},
        },
        at="2026-10-28T12:00:00Z",
        about=("login",),
    )
    index = Index.open(store, load_pack("small", store))
    try:
        index.sync()
        rows, total = query.reversible_decisions(index)
    finally:
        index.close()

    assert total == 1
    assert rows[0]["record_id"] == "01JSPACEDDECISION000000001"
