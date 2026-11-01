"""The single write pipeline: etags, intents, idempotency, records and repair."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store import write as write_module
from context_rail.store.doc import doc_digest, etag, record_digest
from context_rail.store.index import Index, SyncStats
from context_rail.store.location import local_dir
from context_rail.store.query import find
from context_rail.store.write import (
    Op,
    WriteRefused,
    commit,
    repair,
    replay,
    resolve_unique,
)

STAMP = "2026-10-28T00:00:00Z"

VALID_ITEM = b"""---
primitive: item
preset: task
title: C
status: todo
tags: [task]
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
---
# C

A valid task document.
"""

EVENT = {"kind": "doc.created", "about": ["a"], "data": {}}


def _stamp(monkeypatch: pytest.MonkeyPatch, ulid: str, at: str = STAMP) -> None:
    monkeypatch.setattr(write_module, "new_ulid", lambda *_args: ulid)
    monkeypatch.setattr(write_module, "now_iso", lambda: at)


def _intents_dir(store: Path) -> Path:
    return local_dir(store) / "intents"


def _idem_dir(store: Path) -> Path:
    return local_dir(store) / "idem"


def _record_files(store: Path) -> list[Path]:
    return sorted((store / "records").rglob("*.json"))


def _snapshot(store: Path) -> dict[str, str]:
    return {
        path.relative_to(store).as_posix(): sha256_hex(path.read_bytes())
        for path in sorted(store.rglob("*"))
        if path.is_file()
    }


def _craft_intent(store: Path, intent: dict[str, Any]) -> Path:
    path = _intents_dir(store) / f"{intent['intent_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(intent), encoding="utf-8")
    return path


def _intent(
    intent_id: str,
    ops: list[dict[str, Any]],
    result: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 1,
        "intent_id": intent_id,
        "actor": "cto",
        "result": result or {"written": [op["path"] for op in ops], "record": None},
        "ops": ops,
    }
    payload.update(extra)
    return payload


# ── create, etags and refusals ────────────────────────────────────────────


def test_create_with_absent_etag_returns_pinned_shape(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(
        store, manifest, [Op("items/task/a.md", b"v1", "absent")], None, actor="cto"
    )

    assert result == {"written": ["items/task/a.md"], "record": None}
    assert (store / "items/task/a.md").read_bytes() == b"v1"
    assert list(store.rglob("*.tmp")) == []
    assert list(_intents_dir(store).glob("*")) == []


def test_stale_etag_is_refused_and_nothing_written(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    path = "items/task/a.md"
    commit(store, manifest, [Op(path, b"v1", "absent")], None, actor="cto")

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op(path, b"v3", "0000000000000000")],
            None,
            actor="cto",
        )

    assert excinfo.value.code == "CONFLICT"
    assert set(excinfo.value.details) == {"path", "expected", "actual"}
    assert excinfo.value.details["path"] == path
    assert excinfo.value.details["expected"] == "0000000000000000"
    assert (store / path).read_bytes() == b"v1"
    assert list(store.rglob("*.tmp")) == []
    assert list(_intents_dir(store).glob("*")) == []


def test_pre_write_refusals_write_nothing(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    before = _snapshot(store)

    cases = [
        ([Op("items/task/a.md", b"x", None)], "VALIDATION_FAILED"),
        ([Op("../escape.md", b"x", "absent")], "FORBIDDEN"),
        (
            [
                Op("items/task/a.md", b"x", "absent"),
                Op("items/task/a.md", b"y", "absent"),
            ],
            "VALIDATION_FAILED",
        ),
    ]
    for ops, code in cases:
        with pytest.raises(WriteRefused) as excinfo:
            commit(store, manifest, ops, None, actor="cto")
        assert excinfo.value.code == code
        assert _snapshot(store) == before
    assert not (store.parent / "escape.md").exists()


#: True on a filesystem that treats ``A.md`` and ``a.md`` as one file.
_CASE_INSENSITIVE = os.path.normcase("A.md") == os.path.normcase("a.md")


@pytest.mark.skipif(
    not _CASE_INSENSITIVE, reason="this filesystem tells case variants apart"
)
def test_case_variant_duplicate_targets_are_refused_before_any_effect(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    before = _snapshot(store)

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [
                Op("items/task/a.md", b"one", "absent"),
                Op("ITEMS/task/A.md", b"two", "absent"),
            ],
            None,
            actor="cto",
        )

    assert excinfo.value.code == "VALIDATION_FAILED"
    assert excinfo.value.details == {"path": "ITEMS/task/A.md"}
    assert _snapshot(store) == before
    assert list(store.rglob("*.tmp")) == []


def test_escape_is_forbidden(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    with pytest.raises(WriteRefused) as excinfo:
        commit(store, manifest, [Op("../x.md", b"x", "absent")], None, actor="cto")

    assert excinfo.value.code == "FORBIDDEN"
    assert not (store.parent / "x.md").exists()


def test_delete_removes_the_target(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    commit(store, manifest, [Op("items/task/a.md", b"v1", "absent")], None, actor="cto")
    current = etag(b"v1")

    result = commit(
        store, manifest, [Op("items/task/a.md", None, current)], None, actor="cto"
    )

    assert result == {"written": ["items/task/a.md"], "record": None}
    assert not (store / "items/task/a.md").exists()


# ── records ───────────────────────────────────────────────────────────────


def test_record_is_appended_with_valid_digest(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(
        store,
        manifest,
        [Op("items/task/a.md", b"v1", "absent")],
        {"kind": "doc.created", "about": ["a"], "data": {}},
        actor="cto",
    )

    record_id = result["record"]
    assert isinstance(record_id, str) and len(record_id) == 26
    files = _record_files(store)
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    stamp = payload["at"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    assert files[0].relative_to(store).as_posix() == (
        f"records/{stamp[:7]}/{record_id}.json"
    )
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
    assert payload["id"] == record_id
    assert payload["actor"] == "cto"
    assert payload["primitive"] == "record"
    assert payload["preset"] == "event"
    assert payload["kind"] == "doc.created"
    assert payload["about"] == ["a"]
    assert payload["data"] == {}
    assert payload["digest"] == record_digest(payload)
    assert payload["digest"] == record_digest(
        {key: value for key, value in payload.items() if key != "digest"}
    )


def test_record_collision_with_doc_op_is_refused(store_factory, monkeypatch):
    store = store_factory()
    manifest = load_manifest(store)
    _stamp(monkeypatch, "01JCOLLISION00000000000000")
    target = "records/2026-10/01JCOLLISION00000000000000.json"

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op(target, b"doc bytes", "absent")],
            EVENT,
            actor="cto",
        )

    assert excinfo.value.code == "CONFLICT"
    assert not (store / target).exists()


def test_record_collision_with_existing_file_is_refused(store_factory, monkeypatch):
    store = store_factory()
    manifest = load_manifest(store)
    _stamp(monkeypatch, "01JCOLLISION00000000000001")
    target = "records/2026-10/01JCOLLISION00000000000001.json"
    (store / target).parent.mkdir(parents=True)
    (store / target).write_bytes(b"B")

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op("items/task/a.md", b"v1", "absent")],
            EVENT,
            actor="cto",
        )

    assert excinfo.value.code == "CONFLICT"
    assert not (store / "items/task/a.md").exists()
    assert (store / target).read_bytes() == b"B"


def test_record_never_overwritten(store_factory, monkeypatch):
    store = store_factory()
    manifest = load_manifest(store)
    _stamp(monkeypatch, "01JFORCED0000000000000000")
    first = commit(
        store, manifest, [Op("items/task/a.md", b"v1", "absent")], EVENT, actor="cto"
    )
    assert first["record"] == "01JFORCED0000000000000000"

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op("items/task/b.md", b"v1", "absent")],
            {"kind": "doc.created", "about": ["b"], "data": {}},
            actor="cto",
        )

    assert excinfo.value.code == "CONFLICT"
    assert _record_files(store) == [
        store / "records" / STAMP[:7] / "01JFORCED0000000000000000.json"
    ]
    assert not (store / "items/task/b.md").exists()


def test_record_only_commit_appends_one_record(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(store, manifest, [], EVENT, actor="cto")

    assert result["written"] == []
    assert isinstance(result["record"], str)
    assert len(_record_files(store)) == 1


# ── idempotency ───────────────────────────────────────────────────────────


def test_idempotent_key_returns_same_result(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    first = commit(
        store,
        manifest,
        [Op("items/task/b.md", b"x", "absent")],
        {"kind": "doc.created", "about": ["b"], "data": {}},
        actor="cto",
        key="k1",
    )
    second = commit(
        store,
        manifest,
        [Op("items/task/b.md", b"x", "absent")],
        {"kind": "doc.created", "about": ["b"], "data": {}},
        actor="cto",
        key="k1",
    )

    assert first == second
    assert len(_record_files(store)) == 1
    assert (_idem_dir(store) / f"{sha256_hex(b'k1')}.json").is_file()
    assert replay(store, "k1") == first
    assert replay(store, "nope") is None


def test_replay_is_checked_before_the_etag_comparison(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    args = (
        [Op("items/task/b.md", b"x", "absent")],
        {"kind": "doc.created", "about": ["b"], "data": {}},
    )
    first = commit(store, manifest, *args, actor="cto", key="k2")

    again = commit(
        store,
        manifest,
        [Op("items/task/b.md", b"x", "0000000000000000")],
        {"kind": "doc.created", "about": ["b"], "data": {}},
        actor="cto",
        key="k2",
    )

    assert again == first
    assert len(_record_files(store)) == 1


def test_same_key_concurrent_commits_have_one_effect(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def run() -> None:
        barrier.wait()
        try:
            results.append(
                commit(
                    store,
                    manifest,
                    [Op("items/task/c.md", b"y", "absent")],
                    {"kind": "doc.created", "about": ["c"], "data": {}},
                    actor="cto",
                    key="race",
                )
            )
        except BaseException as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert len(_record_files(store)) == 1
    assert (store / "items/task/c.md").read_bytes() == b"y"


def test_idempotency_files_older_than_seven_days_are_pruned(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    folder = _idem_dir(store)
    folder.mkdir(parents=True)
    old = folder / f"{sha256_hex(b'old')}.json"
    recent = folder / f"{sha256_hex(b'recent')}.json"
    old.write_text("{}", encoding="utf-8")
    recent.write_text("{}", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 8 * 86_400, now - 8 * 86_400))
    os.utime(recent, (now - 6 * 86_400, now - 6 * 86_400))

    commit(store, manifest, [Op("items/task/a.md", b"v1", "absent")], None, actor="cto")

    assert not old.exists()
    assert recent.exists()


# ── the index ─────────────────────────────────────────────────────────────


def test_index_is_synced_inside_commit(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    index = Index.open(store, load_pack("basic", store))
    index.sync()
    try:
        commit(
            store,
            manifest,
            [Op("items/task/c.md", VALID_ITEM, "absent")],
            None,
            actor="cto",
            index=index,
        )

        found = find(index, preset="task")
        assert [item["slug"] for item in found["items"]] == ["c"]
        assert index.sync() == SyncStats(0, 0, 0, 0)
    finally:
        index.close()


# ── repair ────────────────────────────────────────────────────────────────


def test_repair_rolls_forward(store_factory):
    store = store_factory()
    intent_id = "01JREPAIR0000000000000000"
    (store / "items/task").mkdir(parents=True)
    (store / "items/task/r.md").write_bytes(b"OLD")
    tmp = f"items/task/r.md.{intent_id}.tmp"
    (store / tmp).write_bytes(b"NEW")
    (store / "items/task/d.md").write_bytes(b"D")
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/r.md",
                    "tmp": tmp,
                    "before_etag": etag(b"OLD"),
                    "after_etag": etag(b"NEW"),
                    "delete": False,
                },
                {
                    "path": "items/task/d.md",
                    "tmp": None,
                    "before_etag": etag(b"D"),
                    "after_etag": "absent",
                    "delete": True,
                },
            ],
            {"written": ["items/task/r.md", "items/task/d.md"], "record": None},
        ),
    )

    report = repair(store)

    assert report["completed"] == [intent_id]
    assert report["unrecoverable"] == []
    assert (store / "items/task/r.md").read_bytes() == b"NEW"
    assert not (store / "items/task/d.md").exists()
    assert not path.exists()
    assert not (store / tmp).exists()


def test_repair_completes_an_already_absent_delete(store_factory):
    store = store_factory()
    intent_id = "01JGONE000000000000000000"
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/gone.md",
                    "tmp": None,
                    "before_etag": etag(b"OLD"),
                    "after_etag": "absent",
                    "delete": True,
                }
            ],
        ),
    )

    report = repair(store)

    assert report["completed"] == [intent_id]
    assert not path.exists()


def test_repair_keeps_an_intervening_edit_and_its_intent(store_factory):
    store = store_factory()
    intent_id = "01JTHIRD00000000000000000"
    (store / "items/task").mkdir(parents=True)
    (store / "items/task/r.md").write_bytes(b"THIRD")
    tmp = f"items/task/r.md.{intent_id}.tmp"
    (store / tmp).write_bytes(b"NEW")
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/r.md",
                    "tmp": tmp,
                    "before_etag": etag(b"OLD"),
                    "after_etag": etag(b"NEW"),
                    "delete": False,
                }
            ],
        ),
    )

    report = repair(store)

    assert report["completed"] == []
    assert {"intent": intent_id, "path": "items/task/r.md"} in report["unrecoverable"]
    assert (store / "items/task/r.md").read_bytes() == b"THIRD"
    assert path.exists()


def test_repair_keeps_an_intervening_delete(store_factory):
    store = store_factory()
    intent_id = "01JDELE000000000000000000"
    (store / "items/task").mkdir(parents=True)
    (store / "items/task/d.md").write_bytes(b"NEWER")
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/d.md",
                    "tmp": None,
                    "before_etag": etag(b"OLD"),
                    "after_etag": "absent",
                    "delete": True,
                }
            ],
        ),
    )

    report = repair(store)

    assert report["completed"] == []
    assert {"intent": intent_id, "path": "items/task/d.md"} in report["unrecoverable"]
    assert (store / "items/task/d.md").read_bytes() == b"NEWER"
    assert path.exists()


def test_repair_reports_unrecoverable(store_factory):
    store = store_factory()
    intent_id = "01JMISSING000000000000000"
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/m.md",
                    "tmp": f"items/task/m.md.{intent_id}.tmp",
                    "before_etag": etag(b"OLD"),
                    "after_etag": etag(b"NEW"),
                    "delete": False,
                }
            ],
        ),
    )

    report = repair(store)

    assert report["completed"] == []
    assert {"intent": intent_id, "path": "items/task/m.md"} in report["unrecoverable"]
    assert path.exists()


def test_repair_accepts_a_minimal_hand_made_intent(store_factory):
    store = store_factory()
    intent_id = "01JMINIMAL000000000000000"
    (store / "items/task").mkdir(parents=True)
    (store / "items/task/r.md").write_bytes(b"OLD")
    tmp = f"items/task/r.md.{intent_id}.tmp"
    (store / tmp).write_bytes(b"NEW")
    path = _intents_dir(store) / f"{intent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "v": 1,
                "intent_id": intent_id,
                "ops": [
                    {
                        "path": "items/task/r.md",
                        "tmp": tmp,
                        "before_etag": etag(b"OLD"),
                        "after_etag": etag(b"NEW"),
                        "delete": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = repair(store)

    assert report["completed"] == [intent_id]
    assert (store / "items/task/r.md").read_bytes() == b"NEW"
    assert not path.exists()


def test_repair_retains_unsupported_and_corrupt_intents(store_factory):
    store = store_factory()
    folder = _intents_dir(store)
    folder.mkdir(parents=True)
    unsupported = folder / "01JVERSION000000000000000.json"
    unsupported.write_text(
        json.dumps({"v": 2, "intent_id": "01JVERSION000000000000000"}),
        encoding="utf-8",
    )
    corrupt = folder / "01JCORRUPT000000000000000.json"
    corrupt.write_text("not json{", encoding="utf-8")

    reports = [repair(store), repair(store)]

    for report in reports:
        assert report["completed"] == []
        assert {entry["intent"] for entry in report["unrecoverable"]} == {
            "01JVERSION000000000000000",
            "01JCORRUPT000000000000000",
        }
    assert unsupported.exists()
    assert corrupt.exists()


def test_repair_clears_stale_publication_temps_only(store_factory):
    store = store_factory()
    folder = _intents_dir(store)
    folder.mkdir(parents=True)
    stale = folder / "01JSTALE00000000000000000.json.4f2a1c9d.tmp"
    stale.write_text("{}", encoding="utf-8")
    published = folder / "01JCORRUPT000000000000000.json"
    published.write_text("not json{", encoding="utf-8")

    report = repair(store)

    assert report["completed"] == []
    assert report["unrecoverable"] == [
        {"intent": "01JCORRUPT000000000000000", "path": None}
    ]
    assert not stale.exists()
    assert published.exists()


def test_missing_staged_temp_is_a_refusal_and_keeps_the_intent(
    store_factory, monkeypatch
):
    store = store_factory()
    manifest = load_manifest(store)
    staged = write_module._stage_temps
    first, second = "items/task/a.md", "items/task/b.md"

    def stage_without_last(store_path, targets, intent_id):
        staged(store_path, targets[:-1], intent_id)

    monkeypatch.setattr(write_module, "_stage_temps", stage_without_last)

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op(first, b"one", "absent"), Op(second, b"two", "absent")],
            None,
            actor="cto",
        )

    assert excinfo.value.code == "INVALID_STATE"
    assert excinfo.value.details == {"path": second}
    assert (store / first).read_bytes() == b"one"
    assert not (store / second).exists()
    intents = list(_intents_dir(store).glob("*.json"))
    assert len(intents) == 1

    report = repair(store)

    intent_id = json.loads(intents[0].read_text(encoding="utf-8"))["intent_id"]
    assert report["completed"] == []
    assert report["unrecoverable"] == [{"intent": intent_id, "path": second}]
    assert intents[0].exists()


def test_replace_oserror_is_a_refusal_and_repair_completes(store_factory, monkeypatch):
    store = store_factory()
    manifest = load_manifest(store)
    first, second = "items/task/a.md", "items/task/b.md"
    real_replace = os.replace
    calls = 0

    def failing_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError(5, "injected replace fault")
        return real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", failing_replace)
        with pytest.raises(WriteRefused) as excinfo:
            commit(
                store,
                manifest,
                [Op(first, b"one", "absent"), Op(second, b"two", "absent")],
                None,
                actor="cto",
            )

    assert excinfo.value.code == "INVALID_STATE"
    assert excinfo.value.details == {"path": second}
    assert (store / first).read_bytes() == b"one"
    assert not (store / second).exists()
    assert len(list(_intents_dir(store).glob("*.json"))) == 1

    report = repair(store)

    assert report["unrecoverable"] == []
    assert (store / second).read_bytes() == b"two"
    assert list(_intents_dir(store).glob("*")) == []


def test_repair_recreates_a_missing_record(store_factory):
    store = store_factory()
    intent_id = "01JRECORD0000000000000000"
    record_bytes = b'{"id": "01J", "primitive": "record"}'
    target = "records/2026-10/01J.json"
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [],
            {"written": [], "record": "01J"},
            record={
                "path": target,
                "bytes_b64": base64.b64encode(record_bytes).decode("ascii"),
                "etag": etag(record_bytes),
            },
        ),
    )

    report = repair(store)

    assert report["completed"] == [intent_id]
    assert (store / target).read_bytes() == record_bytes
    assert not path.exists()


def test_repair_never_overwrites_a_differing_record(store_factory):
    store = store_factory()
    intent_id = "01JRECORD0000000000000001"
    record_bytes = b'{"id": "01J", "primitive": "record"}'
    target = "records/2026-10/01J.json"
    (store / "records/2026-10").mkdir(parents=True)
    (store / target).write_bytes(b'{"id": "other"}')
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [],
            {"written": [], "record": "01J"},
            record={
                "path": target,
                "bytes_b64": base64.b64encode(record_bytes).decode("ascii"),
                "etag": etag(record_bytes),
            },
        ),
    )

    report = repair(store)

    assert report["completed"] == []
    assert {"intent": intent_id, "path": target} in report["unrecoverable"]
    assert (store / target).read_bytes() == b'{"id": "other"}'
    assert path.exists()


def test_repair_publishes_the_saved_idem_result(store_factory):
    store = store_factory()
    intent_id = "01JIDEM00000000000000000"
    key = "k9"
    (store / "items/task").mkdir(parents=True)
    (store / "items/task/s.md").write_bytes(b"NEW")
    record_bytes = b'{"id": "01JR", "primitive": "record"}'
    record_path = "records/2026-10/01JR.json"
    (store / "records/2026-10").mkdir(parents=True)
    (store / record_path).write_bytes(record_bytes)
    result = {"written": ["items/task/s.md"], "record": "01JR"}
    _craft_intent(
        store,
        _intent(
            intent_id,
            [
                {
                    "path": "items/task/s.md",
                    "tmp": f"items/task/s.md.{intent_id}.tmp",
                    "before_etag": etag(b"OLD"),
                    "after_etag": etag(b"NEW"),
                    "delete": False,
                }
            ],
            result,
            key_hash=sha256_hex(key.encode("utf-8")),
            record={
                "path": record_path,
                "bytes_b64": base64.b64encode(record_bytes).decode("ascii"),
                "etag": etag(record_bytes),
            },
        ),
    )

    report = repair(store)

    assert report["completed"] == [intent_id]
    assert replay(store, key) == result


# ── slug resolution ───────────────────────────────────────────────────────


def test_resolve_unique_refuses_ambiguous_slug(indexed_store_with_duplicate):
    with pytest.raises(WriteRefused) as excinfo:
        resolve_unique(indexed_store_with_duplicate.ix, "x")

    assert excinfo.value.code == "CONFLICT"
    assert excinfo.value.details == {
        "slug": "x",
        "paths": ["items/task/x.md", "notes/note/x.md"],
    }


def test_resolve_unique_reports_absent_and_unique_slugs(indexed_store):
    with pytest.raises(LookupError) as excinfo:
        resolve_unique(indexed_store.ix, "nope")

    assert str(excinfo.value) == "not_found"
    assert resolve_unique(indexed_store.ix, "t0") == "items/task/t0.md"


# ── record-preset (markdown) records ──────────────────────────────────────

MARKDOWN_RECORD = {
    "preset": "decision",
    "kind": "decision",
    "title": "Use SQLite",
    "slug": "use-sqlite",
    "body": "# Use SQLite\n\nOne portable file per decision.\n",
    "tags": ["decision"],
    "about": [],
    "data": {},
}


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    _, rest = text.split("---\n", 1)
    raw, body = rest.split("---\n", 1)
    return load_yaml(raw), body


def test_markdown_record_creates_one_document_with_body_digest(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(store, manifest, [], MARKDOWN_RECORD, actor="cto")

    rel = "records/decisions/use-sqlite.md"
    assert result["written"] == [rel]
    assert [path for path in (store / "records").rglob("*") if path.is_file()] == [
        store / rel
    ]
    front, body = _frontmatter(store / rel)
    assert result["record"] == front["id"]
    assert front["primitive"] == "record"
    assert front["preset"] == "decision"
    assert front["title"] == "Use SQLite"
    assert front["tags"] == ["decision"]
    assert body == MARKDOWN_RECORD["body"]
    metadata = {
        key: front[key]
        for key in ("id", "primitive", "preset", "kind", "at", "actor", "about", "data")
    }
    assert front["digest"] == record_digest({**metadata, "body": body})
    assert doc_digest(rel, (store / rel).read_bytes()) == front["digest"]


def test_markdown_record_is_never_overwritten(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    first = commit(store, manifest, [], MARKDOWN_RECORD, actor="cto")
    before = (store / "records/decisions/use-sqlite.md").read_bytes()

    with pytest.raises(WriteRefused) as excinfo:
        commit(store, manifest, [], MARKDOWN_RECORD, actor="cto")

    assert excinfo.value.code == "CONFLICT"
    assert (store / "records/decisions/use-sqlite.md").read_bytes() == before
    assert first["written"] == ["records/decisions/use-sqlite.md"]


def test_markdown_record_syncs_the_index_it_is_given(indexed_store):
    manifest = load_manifest(indexed_store.store)

    commit(
        indexed_store.store,
        manifest,
        [],
        MARKDOWN_RECORD,
        actor="cto",
        index=indexed_store.ix,
    )

    found = find(indexed_store.ix, text="SQLite")
    assert found["total"] == 1
    assert found["items"][0]["path"] == "records/decisions/use-sqlite.md"
    assert found["items"][0]["preset"] == "decision"


def test_repair_recreates_a_missing_markdown_record(store_factory):
    store = store_factory()
    intent_id = "01JMDREPAIR00000000000000"
    record_bytes = (
        b"---\nprimitive: record\npreset: decision\ntitle: Kept\nslug: kept\n---\n# Kept\n"
    )
    target = "records/decisions/kept.md"
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [],
            {"written": [target], "record": "01J"},
            record={
                "path": target,
                "bytes_b64": base64.b64encode(record_bytes).decode("ascii"),
                "etag": etag(record_bytes),
            },
        ),
    )

    report = repair(store)

    assert report == {"completed": [intent_id], "unrecoverable": []}
    assert (store / target).read_bytes() == record_bytes
    assert not path.exists()


def test_json_record_refuses_markdown_only_request_keys(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    request = {"kind": "doc.created", "preset": "event", "body": "# no body here\n"}

    with pytest.raises(WriteRefused) as excinfo:
        commit(store, manifest, [], request, actor="cto")

    assert excinfo.value.code == "VALIDATION_FAILED"
    assert [path for path in (store / "records").rglob("*") if path.is_file()] == []


# ── extra records: one commit, two Records ────────────────────────────────

DECISION = {
    "preset": "decision",
    "kind": "decision.made",
    "about": ["a"],
    "slug": "dependency-login-page",
    "title": "Dependency on login-page",
    "body": "needs the parser",
    "data": {
        "schema": 1,
        "decision_class": "cto-reversible",
        "reason": "needs the parser",
        "before": {"depends_on": []},
        "after": {"depends_on": ["login-page"]},
    },
}


def test_commit_appends_an_extra_record_in_one_commit(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(
        store,
        manifest,
        [Op("items/task/a.md", b"v1", "absent")],
        EVENT,
        actor="cto",
        extra_records=[DECISION],
    )

    event_files = _record_files(store)
    assert len(event_files) == 1
    event = json.loads(event_files[0].read_text(encoding="utf-8"))
    assert result == {"written": ["items/task/a.md"], "record": event["id"]}
    decision = store / "records/decisions/dependency-login-page.md"
    assert decision.is_file()
    assert (store / "items/task/a.md").read_bytes() == b"v1"
    assert list(_intents_dir(store).glob("*")) == []
    assert list(store.rglob("*.tmp")) == []


def test_extra_records_keep_the_result_shape(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    result = commit(
        store,
        manifest,
        [],
        MARKDOWN_RECORD,
        actor="cto",
        extra_records=[{"kind": "doc.created", "about": ["a"], "data": {}}],
    )

    assert set(result) == {"written", "record"}
    assert result["written"] == ["records/decisions/use-sqlite.md"]
    assert len(_record_files(store)) == 1
    assert (store / "records/decisions/use-sqlite.md").is_file()


def test_an_extra_record_colliding_with_an_op_refuses_before_writes(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    target = "records/decisions/use-sqlite.md"

    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [Op(target, b"doc bytes", "absent")],
            None,
            actor="cto",
            extra_records=[
                {"preset": "decision", "kind": "decision.made", "slug": "use-sqlite", "about": [], "data": {}}
            ],
        )

    assert excinfo.value.code == "CONFLICT"
    assert not (store / target).exists()


def test_two_extra_records_claiming_one_path_are_refused(store_factory):
    store = store_factory()
    manifest = load_manifest(store)
    request = {
        "preset": "decision",
        "kind": "decision.made",
        "slug": "one-path",
        "about": [],
        "data": {},
    }

    with pytest.raises(WriteRefused) as excinfo:
        commit(store, manifest, [], None, actor="cto", extra_records=[request, dict(request)])

    assert excinfo.value.code == "CONFLICT"
    assert [path for path in (store / "records").rglob("*") if path.is_file()] == []


def test_extra_records_must_be_mappings(store_factory):
    store = store_factory()
    manifest = load_manifest(store)

    with pytest.raises(WriteRefused) as excinfo:
        commit(store, manifest, [], None, actor="cto", extra_records=["not-a-record"])

    assert excinfo.value.code == "VALIDATION_FAILED"
    assert not (store / "records").exists()


def test_repair_completes_a_second_record_after_a_kill_between_creates(
    store_factory, monkeypatch
):
    store = store_factory()
    manifest = load_manifest(store)
    created: list[str] = []
    real_create = write_module._create_record

    def dying_create(planned):
        created.append(planned.rel)
        if len(created) == 2:
            raise SystemExit(9)
        real_create(planned)

    monkeypatch.setattr(write_module, "_create_record", dying_create)
    with pytest.raises(SystemExit):
        commit(
            store,
            manifest,
            [Op("items/task/a.md", b"v1", "absent")],
            EVENT,
            actor="cto",
            extra_records=[DECISION],
        )

    assert len(created) == 2
    assert len(_record_files(store)) == 1
    assert len(list(_intents_dir(store).glob("*.json"))) == 1

    report = repair(store)

    assert len(report["completed"]) == 1
    assert report["unrecoverable"] == []
    assert list(_intents_dir(store).glob("*")) == []
    assert (store / "items/task/a.md").read_bytes() == b"v1"
    assert len(_record_files(store)) == 1
    assert (store / "records/decisions/dependency-login-page.md").is_file()


def test_an_intent_with_a_malformed_extra_record_entry_is_unrecoverable(store_factory):
    store = store_factory()
    intent_id = "01JEXTRAS0000000000000000"
    path = _craft_intent(
        store,
        _intent(
            intent_id,
            [],
            {"written": [], "record": None},
            extra_records=[{"path": "records/decisions/extra.md", "etag": "0123456789abcdef"}],
        ),
    )

    first = repair(store)
    second = repair(store)

    expected = {"intent": intent_id, "path": None}
    assert expected in first["unrecoverable"]
    assert expected in second["unrecoverable"]
    assert path.exists()
    assert not (store / "records/decisions/extra.md").exists()

