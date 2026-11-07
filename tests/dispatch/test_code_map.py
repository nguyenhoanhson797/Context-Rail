"""The dispatch code-map allowance: valid code notes travel with the candidate."""

from __future__ import annotations

import json
from pathlib import Path

from helpers import init_store

from context_rail.gateway.handlers.dispatch.implementer import _code_map_changes
from context_rail.model.manifest import load_manifest
from context_rail.store.doc import record_digest

STORE = "context-rail"
STORE_PATHS = (STORE, "AGENTS.md", ".agents")
RECORD_ID = "01J00000000000000000000001"


def _note(
    worktree: Path,
    *,
    slug: str = "src-app-py",
    source: str = "src/app.py",
    kind: str = "file",
    preset: str = "code",
) -> Path:
    path = worktree / STORE / "notes" / "code" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "primitive: note",
                f"preset: {preset}",
                f"title: {source}",
                "tags: [code]",
                "created: 2026-11-02T00:00:00Z",
                "updated: 2026-11-02T00:00:00Z",
                f"source_path: {source}",
                f"kind: {kind}",
                "---",
                f"# {source}",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _record(
    worktree: Path,
    *,
    about: str = "src-app-py",
    kind: str = "doc.created",
    record_id: str = RECORD_ID,
) -> Path:
    payload = {
        "id": record_id,
        "primitive": "record",
        "preset": "event",
        "kind": kind,
        "at": "2026-11-02T00:00:00Z",
        "actor": "implementer",
        "about": [about],
        "data": {"path": f"notes/code/{about}.md"},
    }
    payload["digest"] = record_digest(payload)
    path = worktree / STORE / "records" / "2026-11" / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def _changes(*paths: str, complete: bool = True) -> dict:
    return {"store_changed": list(paths), "complete": complete}


def _rel(worktree: Path, path: Path) -> str:
    return path.relative_to(worktree).as_posix()


def test_a_valid_code_note_and_its_record_are_allowed(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree)
    record = _record(worktree)
    note_rel, record_rel = _rel(worktree, note), _rel(worktree, record)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel, record_rel)
    )

    assert refused == []
    assert set(allowed) == {note_rel, record_rel}


def test_a_non_code_store_change_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree)
    record = _record(worktree)
    other = worktree / STORE / "items" / "task" / "worker.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("# worker\n", encoding="utf-8", newline="\n")
    note_rel = _rel(worktree, note)
    record_rel = _rel(worktree, record)
    other_rel = _rel(worktree, other)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel, record_rel, other_rel)
    )

    assert set(allowed) == {note_rel, record_rel}
    assert refused == [other_rel]


def test_a_note_without_source_path_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree, source="")
    note_rel = _rel(worktree, note)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel)
    )

    assert allowed == [] and refused == [note_rel]


def test_a_note_outside_the_code_preset_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree, preset="note")
    note_rel = _rel(worktree, note)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel)
    )

    assert allowed == [] and refused == [note_rel]


def test_a_record_about_another_document_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree)
    valid = _record(worktree)
    wrong = _record(
        worktree, about="other", record_id="01J00000000000000000000002"
    )
    note_rel = _rel(worktree, note)
    valid_rel = _rel(worktree, valid)
    wrong_rel = _rel(worktree, wrong)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel, valid_rel, wrong_rel)
    )

    assert set(allowed) == {note_rel, valid_rel}
    assert refused == [wrong_rel]


def test_a_note_without_its_record_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree)
    note_rel = _rel(worktree, note)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel)
    )

    assert allowed == [] and refused == [note_rel]


def test_a_truncated_capture_refuses_every_store_change(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    worktree = tmp_path / "worktree"
    note = _note(worktree)
    note_rel = _rel(worktree, note)

    allowed, refused = _code_map_changes(
        manifest, worktree, STORE_PATHS, _changes(note_rel, complete=False)
    )

    assert allowed == [] and refused == [note_rel]
