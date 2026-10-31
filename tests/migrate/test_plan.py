from __future__ import annotations

import json
from pathlib import Path

from context_rail.core.util import load_yaml
from context_rail.model.manifest import DEFAULT_MANIFEST, manifest_from_mapping
from context_rail.model.pack import load_pack
from context_rail.store.doc import record_digest
from context_rail.store.legacy import read_legacy
from context_rail.store.migration import plan_migration


def _plan(legacy_root: Path, tmp_path: Path):
    legacy = read_legacy(legacy_root)
    pack = load_pack("small", tmp_path / "none")
    manifest = manifest_from_mapping({**DEFAULT_MANIFEST, "pack": "small"}, tmp_path / "t")
    return plan_migration(
        legacy, pack=pack, manifest=manifest, at="2026-02-01T00:00:00Z"
    )


def _front(data: bytes) -> dict:
    text = data.decode("utf-8")
    block = text.split("---\n", 2)[1]
    return load_yaml(block)


def test_the_mapping_table_presets_statuses_and_links(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    plan = _plan(legacy_v2_store, tmp_path)
    by_source = {doc.source: doc for doc in plan.documents}
    login = _front(by_source["what/tasks/login.md"].data)
    assert login["primitive"] == "item" and login["preset"] == "task"
    assert login["status"] == "doing"
    assert login["legacy_type"] == "task"
    assert login["part_of"] == ["[[sc-01]]"]
    assert "legacy" in login["tags"]
    assert _front(by_source["what/requirements/req-1.md"].data)["preset"] == "task"
    assert _front(by_source["context/Goal.md"].data)["preset"] == "goal"
    assert _front(by_source["context/scope/SC-01.md"].data)["preset"] == "scope"
    assert _front(by_source["when/phases/phase-1.md"].data)["preset"] == "note"
    assert _front(by_source["history/decisions/use-sqlite.md"].data)["preset"] == "note"
    assert by_source["what/tasks/login.md"].rel == "items/task/login.md"


def test_legacy_events_become_valid_immutable_records(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    plan = _plan(legacy_v2_store, tmp_path)
    assert [record.event_id for record in plan.records] == [
        "01JLEGACYEVENT00000000001",
        "01JLEGACYEVENT00000000002",
    ]
    first = plan.records[0]
    assert first.rel == "records/2026-01/01JLEGACYEVENT00000000001.json"
    payload = json.loads(first.data)
    assert payload["kind"] == "legacy.task.status"
    assert payload["about"] == ["login"]
    assert payload["digest"] == record_digest(payload)


def test_v1_documents_keep_their_wiki_types(
    legacy_v1_store: Path, tmp_path: Path
) -> None:
    plan = _plan(legacy_v1_store, tmp_path)
    by_source = {doc.source: doc for doc in plan.documents}
    assert _front(by_source["02-wiki/Goal.md"].data)["preset"] == "goal"
    assert _front(by_source["01-docs/tasks/old-task.md"].data)["preset"] == "task"
    assert plan.records == ()
