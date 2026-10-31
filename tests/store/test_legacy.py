from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_rail.store.legacy import read_legacy
from context_rail.store.write import WriteRefused


def test_v2_is_detected_with_types_links_and_events(legacy_v2_store: Path) -> None:
    store = read_legacy(legacy_v2_store)
    assert store.schema == 2
    assert store.registry["project_id"] == "legacy-fixture"
    by_source = {doc.source: doc for doc in store.documents}
    login = by_source["what/tasks/login.md"]
    assert login.legacy_type == "task"
    assert login.title == "Login page"
    assert login.status == "doing"
    assert login.links == (("part_of", "SC-01"),)
    assert "[[SC-01]]" in login.body
    assert by_source["what/requirements/req-1.md"].legacy_type == "requirement"
    assert by_source["context/Goal.md"].created == "2026-01-01"
    assert [event.id for event in store.events] == [
        "01JLEGACYEVENT00000000001",
        "01JLEGACYEVENT00000000002",
    ]
    assert store.warnings == ()


def test_v1_is_detected_and_wiki_names_map_to_types(legacy_v1_store: Path) -> None:
    store = read_legacy(legacy_v1_store)
    assert store.schema == 1
    by_source = {doc.source: doc for doc in store.documents}
    assert by_source["01-docs/tasks/old-task.md"].legacy_type == "task"
    assert by_source["02-wiki/Goal.md"].legacy_type == "goal"
    assert store.registry["core_types"] == ["task"]


def test_a_v3_store_and_an_unknown_schema_are_refused(tmp_path: Path) -> None:
    v3 = tmp_path / "v3"
    v3.mkdir()
    (v3 / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    with pytest.raises(WriteRefused) as caught:
        read_legacy(v3)
    assert caught.value.code == "VALIDATION_FAILED"

    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "rail.json").write_text(
        json.dumps({"schema_version": 9}), encoding="utf-8"
    )
    with pytest.raises(WriteRefused) as caught:
        read_legacy(unknown)
    assert caught.value.code == "SCHEMA_UNSUPPORTED"


def test_an_unreadable_document_is_kept_as_a_warning(legacy_v2_store: Path) -> None:
    broken = legacy_v2_store / "what/tasks/broken.md"
    broken.write_text("---\ntitle: [unclosed\n---\n# Broken\n", encoding="utf-8")
    store = read_legacy(legacy_v2_store)
    assert any("broken.md" in warning for warning in store.warnings)
    doc = next(doc for doc in store.documents if doc.source.endswith("broken.md"))
    assert doc.legacy_type == "task"
    assert doc.title == "broken"


def test_a_byte_order_mark_does_not_hide_a_legacy_store(legacy_v2_store: Path) -> None:
    for rel in ("rail.json", "log/2026-01/01JLEGACYEVENT00000000001.json"):
        path = legacy_v2_store / rel
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    store = read_legacy(legacy_v2_store)
    assert store.schema == 2
    assert store.registry["project_id"] == "legacy-fixture"
    assert store.events[0].id == "01JLEGACYEVENT00000000001"


def test_duplicate_event_ids_keep_the_first(legacy_v2_store: Path) -> None:
    journal = legacy_v2_store / "log.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8")
        + '{"id": "01JLEGACYEVENT00000000001", "at": "2026-01-09T00:00:00Z", '
        '"kind": "duplicate", "subject": "x", "actor": "a", "data": {}}\n',
        encoding="utf-8",
    )
    store = read_legacy(legacy_v2_store)
    assert [event.kind for event in store.events] == ["task.status", "check.tick"]
