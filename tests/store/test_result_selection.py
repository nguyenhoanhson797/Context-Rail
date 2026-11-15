"""Result record projection, selection, cycle derivation and recovery tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.core.util import dump_frontmatter, new_ulid, sha256_hex
from datetime import datetime, timezone
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from context_rail.model.results import (
    RESULT_ADOPTION_KIND,
    RESULT_BODY_MAX_BYTES,
    RESULT_KIND,
    RESULT_OUTCOMES,
    RESULT_SCHEMA,
    ResultBinding,
    ResultPayload,
    ResultSelection,
    parse_result,
    result_body_hash,
    validate_result_body,
)
from context_rail.store import query
from context_rail.store.approval import semantic_hash
from context_rail.store.doc import record_digest
from context_rail.store.index import INDEX_SCHEMA, Index
from context_rail.store.location import index_path
from context_rail.store.results import select_result, work_cycle
from helpers import (
    BUGFIX_PRODUCER_SECTIONS,
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    init_result_store,
    result_body,
    write_candidate,
    write_record,
    write_review_verdict,
)


class _Ctx:
    """Minimal store context for store.results unit tests."""

    def __init__(self, store: Path, pack_name: str = "result-test") -> None:
        from context_rail.model.compiler import _placement, compile_project
        from context_rail.model.manifest import load_manifest
        from context_rail.model.presentation import load_presentation

        self.store = Path(store)
        self.manifest = load_manifest(self.store)
        self.pack = load_pack(pack_name, self.store)
        self.index = Index.open(self.store, self.pack)
        self.index.sync()
        presentation = load_presentation(self.pack.name, self.store, self.pack)
        self.model = compile_project(
            self.pack,
            self.pack.operating,
            presentation,
            placement=_placement(self.manifest),
        )

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> "_Ctx":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _write_item(
    store: Path,
    slug: str,
    *,
    preset: str = "task",
    status: str = "doing",
    title: str = "Investigate issue",
    objective: str = "Confirm root cause.",
    acceptance: str = "Document findings and evidence.",
    updated: str = "2026-11-15T10:00:00Z",
) -> Path:
    rel = f"items/{preset}/{slug}.md"
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    front = {
        "primitive": "item",
        "preset": preset,
        "title": title,
        "status": status,
        "tags": [preset],
        "created": "2026-11-15T09:00:00Z",
        "updated": updated,
    }
    body = f"# {title}\n\n## Objective\n{objective}\n\n## Acceptance\n{acceptance}\n"
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    return path


def _item_digest(store: Path, rel: str) -> str:
    return semantic_hash((store / rel).read_bytes(), format="markdown")


def _write_result_record(
    store: Path,
    *,
    record_id: str | None = None,
    item: str = "task-1",
    item_rel: str = "items/task/task-1.md",
    workflow_name: str = "sprint",
    preset: str = "finding",
    attempt: int = 1,
    actor: str = "implementer",
    at: str = "2026-11-15T10:05:00Z",
    outcome: str = "completed",
    candidate_commit: str | None = None,
    body: str | None = None,
    contract_digest: str | None = None,
    item_digest_override: str | None = None,
    cycle: str | None = None,
    data_overrides: Mapping[str, Any] | None = None,
    front_overrides: Mapping[str, Any] | None = None,
    about: list[str] | None = None,
    tamper_front_digest: bool = False,
) -> tuple[str, Path, str]:
    rid = record_id or new_ulid()
    with _Ctx(store) as ctx:
        compiled = ctx.model.results[workflow_name].produce
        sections = compiled.sections
        digest_contract = contract_digest or compiled.digest
    text_body = body if body is not None else result_body(sections)
    normalized_body = text_body.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    bhash = result_body_hash(normalized_body)
    idigest = item_digest_override or _item_digest(store, item_rel)
    data: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "item": item,
        "attempt": attempt,
        "actor": actor,
        "at": at,
        "outcome": outcome,
        "candidate_commit": candidate_commit,
        "body_hash": bhash,
        "item_digest": idigest,
        "work_cycle": cycle,
        "contract_digest": digest_contract,
    }
    if data_overrides:
        data.update(data_overrides)
    rel = f"records/findings/{workflow_name}/{item}/{rid}.md"
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    front: dict[str, Any] = {
        "primitive": "record",
        "preset": preset,
        "title": f"Result {attempt} for {item}",
        "tags": ["result"],
        "id": rid,
        "kind": RESULT_KIND,
        "at": at,
        "actor": actor,
        "about": [item] if about is None else list(about),
        "data": data,
        "workflow_name": workflow_name,
    }
    if front_overrides:
        front.update(front_overrides)
    if tamper_front_digest:
        front["digest"] = "0" * 64
    else:
        front["digest"] = record_digest(front)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + text_body,
        encoding="utf-8",
        newline="\n",
    )
    return rid, path, bhash


def _write_status_changed(
    store: Path,
    *,
    record_id: str,
    item: str,
    from_status: str,
    to_status: str,
    at: str,
    workflow: str = "sprint",
    from_category: str | None = None,
    to_category: str | None = None,
) -> Path:
    data: dict[str, Any] = {
        "from": from_status,
        "to": to_status,
        "resolution": None,
        "workflow": workflow,
    }
    if from_category is not None:
        data["from_category"] = from_category
    if to_category is not None:
        data["to_category"] = to_category
    return write_record(
        store,
        record_id,
        "status.changed",
        data,
        at=at,
        actor="cto",
        about=(item,),
    )


def test_result_projection_orders_by_envelope_at_desc_then_record_id_desc(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    r_old_a = _write_result_record(
        store,
        record_id="01JRES0000000000000000000A",
        attempt=1,
        at="2026-11-15T10:00:00Z",
    )[0]
    r_same_low = _write_result_record(
        store,
        record_id="01JRES0000000000000000000B",
        attempt=2,
        at="2026-11-15T11:00:00Z",
    )[0]
    r_same_high = _write_result_record(
        store,
        record_id="01JRES0000000000000000000C",
        attempt=3,
        at="2026-11-15T11:00:00Z",
        # Payload clock differs slightly, but envelope ``at`` governs ordering.
        data_overrides={"at": "2026-11-15T10:59:59Z"},
    )[0]

    with _Ctx(store) as ctx:
        rows = query.results_for_item(ctx.index, "task-1")
        assert [row["record_id"] for row in rows] == [
            r_same_high,
            r_same_low,
            r_old_a,
        ]
        assert query.result_count(ctx.index, "task-1") == 3
        limited = query.results_for_item(ctx.index, "task-1", limit=2)
        assert [row["record_id"] for row in limited] == [r_same_high, r_same_low]
        by_id = query.result_by_record(ctx.index, r_same_low)
        assert by_id is not None
        assert by_id["at"] == "2026-11-15T11:00:00Z"
        assert by_id["actor"] == "implementer"
        assert by_id["item"] == "task-1"
        assert by_id["data"]["attempt"] == 2
        assert query.result_by_record(ctx.index, "01JRESMISSING0000000000000") is None


def test_producer_and_reviewer_channels_stay_separate(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    rid, _, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000001",
        at="2026-11-15T10:00:00Z",
        outcome="completed",
    )
    # A later reviewer verdict (including Markdown review note) must never enter results table
    review_id = "01JREV00000000000000000002"
    rev_path = store / "records" / "assessments" / "sprint" / "task-1" / f"{review_id}.md"
    rev_path.parent.mkdir(parents=True, exist_ok=True)
    rev_front = {
        "primitive": "record",
        "preset": "assessment",
        "title": "Review",
        "tags": ["review"],
        "id": review_id,
        "kind": "review.verdict",
        "at": "2026-11-15T12:00:00Z",
        "actor": "reviewer",
        "about": ["task-1"],
        "data": {
            "schema": 1,
            "item": "task-1",
            "reviewer_actor": "reviewer",
            "candidate_commit": "a" * 40,
            "verdict": "approve",
        },
        "workflow_name": "sprint",
    }
    rev_front["digest"] = record_digest(rev_front)
    rev_path.write_text(
        "---\n" + dump_frontmatter(rev_front) + "---\n" + result_body(DEFAULT_REVIEW_SECTIONS),
        encoding="utf-8",
        newline="\n",
    )

    with _Ctx(store) as ctx:
        assert query.result_count(ctx.index, "task-1") == 1
        assert [r["record_id"] for r in query.results_for_item(ctx.index, "task-1")] == [rid]
        selected = select_result(ctx, "task-1")
        assert selected.current is True
        assert selected.completed is True
        assert selected.binding is not None
        assert selected.binding.record == rid


def test_newer_partial_blocked_failed_supersedes_completed(tmp_path: Path) -> None:
    for bad_outcome in ("partial", "blocked", "failed"):
        sub = tmp_path / bad_outcome
        store = init_result_store(sub)
        _write_item(store, "task-1")
        _write_result_record(
            store,
            record_id="01JRES00000000000000000001",
            attempt=1,
            at="2026-11-15T10:00:00Z",
            outcome="completed",
        )
        newer_id, _, _ = _write_result_record(
            store,
            record_id="01JRES00000000000000000002",
            attempt=2,
            at="2026-11-15T11:00:00Z",
            outcome=bad_outcome,
        )
        with _Ctx(store) as ctx:
            selected = select_result(ctx, "task-1")
            assert selected.record is not None
            assert selected.record["record_id"] == newer_id
            assert selected.record["data"]["outcome"] == bad_outcome
            assert selected.current is True
            assert selected.completed is False
            assert any(bad_outcome in r or "completed" in r or "outcome" in r for r in selected.reasons)


def test_candidate_binding_mismatch_and_later_unbound_result_refused(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    sha_a = "a" * 40
    sha_b = "b" * 40
    write_candidate(
        store, "01JCAND0000000000000000001", "task-1", sha_a, at="2026-11-15T09:30:00Z"
    )
    r_old_a, _, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000001",
        attempt=1,
        at="2026-11-15T10:00:00Z",
        candidate_commit=sha_a,
    )
    # Candidate B is recorded later; old result bound to A is stale for B
    write_candidate(
        store, "01JCAND0000000000000000002", "task-1", sha_b, attempt=2, at="2026-11-15T11:00:00Z"
    )
    with _Ctx(store) as ctx:
        stale_for_b = select_result(ctx, "task-1")
        assert stale_for_b.current is False
        assert stale_for_b.completed is False
        assert stale_for_b.binding is not None
        assert stale_for_b.binding.candidate_commit == sha_a
        assert any("candidate" in r or "commit" in r for r in stale_for_b.reasons)

        # Explicit older query can still inspect r_old_a for candidate A diagnostically
        explicit_a = select_result(ctx, "task-1", record_id=r_old_a, candidate_commit=sha_a)
        assert explicit_a.record is not None
        assert explicit_a.record["record_id"] == r_old_a
        assert explicit_a.current is False
        assert explicit_a.completed is False

    # A later result with candidate_commit=None cannot compensate for missing candidate B
    r_unbound, _, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000003",
        attempt=2,
        at="2026-11-15T12:00:00Z",
        candidate_commit=None,
    )
    with _Ctx(store) as ctx:
        unbound = select_result(ctx, "task-1")
        assert unbound.record is not None
        assert unbound.record["record_id"] == r_unbound
        assert unbound.current is False
        assert unbound.completed is False
        assert any("candidate" in r or "commit" in r for r in unbound.reasons)


def test_wrong_item_explicit_record_refused(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    _write_item(store, "task-2")
    r_other, _, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000001",
        item="task-2",
        item_rel="items/task/task-2.md",
    )
    with _Ctx(store) as ctx:
        selected = select_result(ctx, "task-1", record_id=r_other)
        assert selected.current is False
        assert selected.completed is False
        assert any("item" in r for r in selected.reasons)


def test_item_content_edit_invalidates_while_lifecycle_move_preserves(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    item_path = _write_item(store, "task-1", status="doing")
    _write_result_record(store, record_id="01JRES00000000000000000001")

    # Lifecycle-only change (status/updated) preserves semantic_hash -> still current
    raw = item_path.read_text(encoding="utf-8")
    item_path.write_text(
        raw.replace("status: doing", "status: done").replace(
            "updated: '2026-11-15T10:00:00Z'", "updated: '2026-11-15T10:30:00Z'"
        ),
        encoding="utf-8",
        newline="\n",
    )
    with _Ctx(store) as ctx:
        moved = select_result(ctx, "task-1")
        assert moved.current is True
        assert moved.completed is True

    # Objective / acceptance edit changes item_digest -> invalidates proof
    edited = item_path.read_text(encoding="utf-8").replace(
        "Confirm root cause.", "Confirm root cause and patch edge cases."
    )
    item_path.write_text(edited, encoding="utf-8", newline="\n")
    with _Ctx(store) as ctx:
        stale = select_result(ctx, "task-1")
        assert stale.current is False
        assert stale.completed is False
        assert any("item" in r and ("digest" in r or "content" in r or "changed" in r) for r in stale.reasons)


def test_contract_drift_invalidates_result(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    _write_result_record(store, record_id="01JRES00000000000000000001")

    pack_path = store / "packs" / "result-test" / "pack.yaml"
    pack_doc = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_doc["results"]["produce"]["sections"] = [
        "Summary",
        "Findings",
        "Evidence",
        "Recommendations",
    ]
    pack_path.write_text(
        yaml.safe_dump(pack_doc, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with _Ctx(store) as ctx:
        drifted = select_result(ctx, "task-1")
        assert drifted.current is False
        assert drifted.completed is False
        assert any("contract" in r for r in drifted.reasons)


def test_reopened_container_invalidates_result_and_updates_work_cycle(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "phase-1", preset="phase", status="todo")
    with _Ctx(store) as ctx:
        assert work_cycle(ctx, "phase-1") is None

    _write_status_changed(
        store,
        record_id="01JEV000000000000000000001",
        item="phase-1",
        from_status="todo",
        to_status="done",
        at="2026-11-15T10:00:00Z",
        workflow="default",
    )
    # Initial completion from unfinished -> finished does not start a reopen cycle
    with _Ctx(store) as ctx:
        assert work_cycle(ctx, "phase-1") is None

    _write_result_record(
        store,
        record_id="01JRES00000000000000000001",
        item="phase-1",
        item_rel="items/phase/phase-1.md",
        workflow_name="default",
        at="2026-11-15T10:05:00Z",
        cycle=None,
    )
    with _Ctx(store) as ctx:
        first = select_result(ctx, "phase-1")
        assert first.current is True
        assert first.completed is True
        assert first.binding is not None
        assert first.binding.work_cycle is None

    # Reopen: finished (done) -> unfinished (todo) starts a new cycle = event ULID
    reopen_ulid = "01JEV000000000000000000002"
    _write_status_changed(
        store,
        record_id=reopen_ulid,
        item="phase-1",
        from_status="done",
        to_status="todo",
        at="2026-11-15T11:00:00Z",
        workflow="default",
    )
    with _Ctx(store) as ctx:
        current_cycle = work_cycle(ctx, "phase-1")
        assert current_cycle == reopen_ulid
        reopened = select_result(ctx, "phase-1")
        assert reopened.current is False
        assert reopened.completed is False
        assert reopened.binding is not None
        assert reopened.binding.work_cycle != current_cycle
        assert any("cycle" in r for r in reopened.reasons)


def test_work_cycle_uses_pack_changed_finished_to_unfinished_reset(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1", status="done")
    pack_change_id = "01JPK000000000000000000001"
    write_record(
        store,
        pack_change_id,
        "pack.changed",
        {
            "schema": 1,
            "from": "old",
            "to": "result-test",
            "status_map": {"done": "doing"},
            "changes": [
                {
                    "slug": "task-1",
                    "from": "done",
                    "to": "doing",
                    "from_category": "done",
                    "to_category": "active",
                }
            ],
            "changed_slugs": ["task-1"],
            "changed_count": 1,
            "workflow_changes": [],
            "approval_target": "pack:result-test",
            "candidate_snapshot": {},
            "candidate_digest": "a" * 64,
        },
        at="2026-11-15T11:00:00Z",
        about=("task-1",),
    )
    with _Ctx(store) as ctx:
        assert work_cycle(ctx, "task-1") == pack_change_id


def test_altered_or_malformed_newest_result_blocks_fallback_to_older_completed(
    tmp_path: Path,
) -> None:
    for mode in ("body_tamper", "front_digest_tamper", "bad_payload", "identity_mismatch", "missing_body_heading"):
        sub = tmp_path / mode
        store = init_result_store(sub)
        _write_item(store, "task-1")
        _write_result_record(
            store,
            record_id="01JRES00000000000000000001",
            attempt=1,
            at="2026-11-15T10:00:00Z",
            outcome="completed",
        )
        bad_id = "01JRES00000000000000000002"
        if mode == "body_tamper":
            _, path, _ = _write_result_record(
                store,
                record_id=bad_id,
                attempt=2,
                at="2026-11-15T11:00:00Z",
            )
            path.write_text(
                path.read_text(encoding="utf-8") + "\nTampered trailing text.\n",
                encoding="utf-8",
                newline="\n",
            )
        elif mode == "front_digest_tamper":
            _write_result_record(
                store,
                record_id=bad_id,
                attempt=2,
                at="2026-11-15T11:00:00Z",
                tamper_front_digest=True,
            )
        elif mode == "bad_payload":
            _write_result_record(
                store,
                record_id=bad_id,
                attempt=2,
                at="2026-11-15T11:00:00Z",
                data_overrides={"smuggled": True},
            )
        elif mode == "identity_mismatch":
            _write_result_record(
                store,
                record_id=bad_id,
                attempt=2,
                at="2026-11-15T11:00:00Z",
                data_overrides={"actor": "other-agent"},
            )
        elif mode == "missing_body_heading":
            _write_result_record(
                store,
                record_id=bad_id,
                attempt=2,
                at="2026-11-15T11:00:00Z",
                body="## Summary\nOnly summary present.\n",
            )

        with _Ctx(store) as ctx:
            selected = select_result(ctx, "task-1")
            assert selected.current is False, mode
            assert selected.completed is False, mode
            assert selected.reasons, mode
            if mode == "bad_payload":
                problems = ctx.index.conn.execute(
                    "SELECT problems, broken FROM docs WHERE slug = ?", (bad_id,)
                ).fetchone()
                assert problems is not None
                assert "invalid_payload" in json.loads(problems[0])
                assert problems[1] == 0


def test_result_cache_delete_edit_and_full_rebuild_from_files(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    r1, p1, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000001",
        attempt=1,
        at="2026-11-15T10:00:00Z",
        outcome="completed",
    )
    r2, p2, _ = _write_result_record(
        store,
        record_id="01JRES00000000000000000002",
        attempt=2,
        at="2026-11-15T11:00:00Z",
        outcome="partial",
    )

    with _Ctx(store) as ctx:
        assert query.result_count(ctx.index, "task-1") == 2
        assert select_result(ctx, "task-1").record["record_id"] == r2

    # Record a review binding and an adoption snapshot to verify both rebuild cleanly
    rev_id = "01JREV00000000000000000009"
    write_record(
        store,
        rev_id,
        "review.verdict",
        {
            "schema": 1,
            "item": "task-1",
            "reviewer_actor": "reviewer",
            "candidate_commit": None,
            "result_record": r1,
            "result_body_hash": "a" * 64,
            "verdict": "approve",
        },
        at="2026-11-15T10:10:00Z",
        actor="reviewer",
        about=("task-1",),
    )
    adopt_id = "01JADOPT000000000000000001"
    write_record(
        store,
        adopt_id,
        RESULT_ADOPTION_KIND,
        {
            "schema": 1,
            "workflow": "sprint",
            "items": [
                {
                    "item": "task-1",
                    "item_digest": _item_digest(store, "items/task/task-1.md"),
                    "work_cycle": None,
                }
            ],
        },
        at="2026-11-15T09:00:00Z",
        actor="human",
        about=("task-1",),
    )

    # Edit p2 body in place -> mtime/size changes -> sync catches tampering -> failed diagnostic
    p2.write_text(
        p2.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8", newline="\n"
    )
    with _Ctx(store) as ctx:
        edited = select_result(ctx, "task-1")
        assert edited.current is False
        assert edited.completed is False

    # Delete p2 -> sync removes r2 row -> r1 becomes newest again
    p2.unlink()
    with _Ctx(store) as ctx:
        assert query.result_count(ctx.index, "task-1") == 1
        after_del = select_result(ctx, "task-1")
        assert after_del.record["record_id"] == r1
        assert after_del.current is True
        assert after_del.completed is True

    # Delete SQLite cache and rebuild from file truth
    index_path(store).unlink()
    with _Ctx(store) as ctx:
        assert query.result_count(ctx.index, "task-1") == 1
        rebuilt = select_result(ctx, "task-1")
        assert rebuilt.record["record_id"] == r1
        assert rebuilt.current is True
        assert rebuilt.completed is True
        rev_row = query.review_for_result(ctx.index, "task-1", r1)
        assert rev_row is not None
        assert rev_row["record_id"] == rev_id
        assert rev_row["result_record"] == r1
        assert rev_row["result_body_hash"] == "a" * 64
        adopt_row = query.result_adoption_for(ctx.index, "task-1", "sprint")
        assert adopt_row is not None
        assert adopt_row["record_id"] == adopt_id
        assert adopt_row["item_digest"] == _item_digest(store, "items/task/task-1.md")


def _ulid_at(iso_utc: str) -> str:
    ms = int(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp() * 1000)
    return new_ulid(ms=ms)


def test_select_result_never_runs_git_or_checks_or_hashes_source_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from context_rail.runner import git as runner_git

    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    _write_result_record(store, record_id="01JRES00000000000000000001")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("reads must not run git or subprocesses")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(runner_git, "diff_names", boom)

    with _Ctx(store) as ctx:
        selected = select_result(ctx, "task-1")
        assert selected.current is True
        assert selected.completed is True


def test_newer_broken_frontmatter_result_fails_closed_after_completed(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    old_id = _ulid_at("2026-11-15T10:00:00Z")
    _write_result_record(
        store,
        record_id=old_id,
        attempt=1,
        at="2026-11-15T10:00:00Z",
        outcome="completed",
    )
    bad_id = _ulid_at("2026-11-15T11:00:00Z")
    bad_path = store / "records" / "findings" / "sprint" / "task-1" / f"{bad_id}.md"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(
        "---\nkind: result.recorded\nabout: [task-1\n---\n## Summary\nBroken YAML.\n",
        encoding="utf-8",
        newline="\n",
    )

    with _Ctx(store) as ctx:
        rows = query.results_for_item(ctx.index, "task-1")
        assert [r["record_id"] for r in rows] == [bad_id, old_id]
        assert rows[0]["at"] is None
        assert rows[0]["valid"] is False
        selected = select_result(ctx, "task-1")
        assert selected.record is not None
        assert selected.record["record_id"] == bad_id
        assert selected.current is False
        assert selected.completed is False
        assert any("parse_error" in r or "broken" in r or "problem" in r for r in selected.reasons)


def test_newer_missing_or_invalid_envelope_at_fails_closed(tmp_path: Path) -> None:
    for mode in ("missing_at", "invalid_at"):
        sub = tmp_path / mode
        store = init_result_store(sub)
        _write_item(store, "task-1")
        old_id = _ulid_at("2026-11-15T10:00:00Z")
        _write_result_record(
            store,
            record_id=old_id,
            attempt=1,
            at="2026-11-15T10:00:00Z",
            outcome="completed",
        )
        bad_id = _ulid_at("2026-11-15T11:00:00Z")
        front_overrides: dict[str, Any] = (
            {"at": None} if mode == "missing_at" else {"at": "not-an-iso-timestamp"}
        )
        _write_result_record(
            store,
            record_id=bad_id,
            attempt=2,
            at="2026-11-15T11:00:00Z",
            front_overrides=front_overrides,
        )
        with _Ctx(store) as ctx:
            rows = query.results_for_item(ctx.index, "task-1")
            assert [r["record_id"] for r in rows] == [bad_id, old_id], mode
            assert rows[0]["at"] is None, mode
            selected = select_result(ctx, "task-1")
            assert selected.record is not None
            assert selected.record["record_id"] == bad_id, mode
            assert selected.current is False, mode
            assert selected.completed is False, mode
            assert selected.reasons, mode


def test_older_broken_result_does_not_hide_newer_completed_result(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    old_bad_id = _ulid_at("2026-11-15T09:00:00Z")
    bad_path = store / "records" / "findings" / "sprint" / "task-1" / f"{old_bad_id}.md"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(
        "---\nkind: result.recorded\nabout: [task-1\n---\n## Summary\nOld broken.\n",
        encoding="utf-8",
        newline="\n",
    )
    newer_id = _ulid_at("2026-11-15T11:00:00Z")
    _write_result_record(
        store,
        record_id=newer_id,
        attempt=2,
        at="2026-11-15T11:00:00Z",
        outcome="completed",
    )

    with _Ctx(store) as ctx:
        rows = query.results_for_item(ctx.index, "task-1")
        assert [r["record_id"] for r in rows] == [newer_id, old_bad_id]
        selected = select_result(ctx, "task-1")
        assert selected.record is not None
        assert selected.record["record_id"] == newer_id
        assert selected.current is True
        assert selected.completed is True


def test_broken_reviewer_markdown_never_enters_producer_results(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    prod_id = _ulid_at("2026-11-15T10:00:00Z")
    _write_result_record(
        store,
        record_id=prod_id,
        attempt=1,
        at="2026-11-15T10:00:00Z",
        outcome="completed",
    )
    rev_id = _ulid_at("2026-11-15T12:00:00Z")
    rev_path = store / "records" / "assessments" / "sprint" / "task-1" / f"{rev_id}.md"
    rev_path.parent.mkdir(parents=True, exist_ok=True)
    rev_path.write_text(
        "---\nkind: review.verdict\nabout: [task-1\n---\n## Summary\nBroken review.\n",
        encoding="utf-8",
        newline="\n",
    )

    with _Ctx(store) as ctx:
        assert query.result_count(ctx.index, "task-1") == 1
        assert [r["record_id"] for r in query.results_for_item(ctx.index, "task-1")] == [prod_id]
        selected = select_result(ctx, "task-1")
        assert selected.current is True
        assert selected.completed is True
        assert selected.record["record_id"] == prod_id


def test_broken_and_valid_results_at_shallow_allowed_placement_depth(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    manifest_path = store / "rail.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["presets"]["finding"] = "records/findings/{id}.md"
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8", newline="\n"
    )
    _write_item(store, "task-1")
    with _Ctx(store) as ctx:
        compiled = ctx.model.results["sprint"].produce
        sections = compiled.sections
        digest_contract = compiled.digest

    good_id = _ulid_at("2026-11-15T10:00:00Z")
    text_body = result_body(sections)
    data_good = {
        "schema": RESULT_SCHEMA,
        "item": "task-1",
        "attempt": 1,
        "actor": "implementer",
        "at": "2026-11-15T10:00:00Z",
        "outcome": "completed",
        "candidate_commit": None,
        "body_hash": result_body_hash(text_body),
        "item_digest": _item_digest(store, "items/task/task-1.md"),
        "work_cycle": None,
        "contract_digest": digest_contract,
    }
    front_good = {
        "primitive": "record",
        "preset": "finding",
        "title": "Shallow result",
        "tags": ["result"],
        "id": good_id,
        "kind": RESULT_KIND,
        "at": "2026-11-15T10:00:00Z",
        "actor": "implementer",
        "about": ["task-1"],
        "data": data_good,
        "workflow_name": "sprint",
    }
    front_good["digest"] = record_digest(front_good)
    shallow_good = store / "records" / "findings" / f"{good_id}.md"
    shallow_good.parent.mkdir(parents=True, exist_ok=True)
    shallow_good.write_text(
        "---\n" + dump_frontmatter(front_good) + "---\n" + text_body,
        encoding="utf-8",
        newline="\n",
    )
    with _Ctx(store) as ctx:
        selected = select_result(ctx, "task-1")
        assert selected.current is True
        assert selected.completed is True
        assert selected.record["record_id"] == good_id

    # A broken producer result at that shallower placement is also caught as a stub
    bad_id = _ulid_at("2026-11-15T11:00:00Z")
    shallow_bad = store / "records" / "findings" / f"{bad_id}.md"
    shallow_bad.write_text(
        "---\nkind: result.recorded\nabout: [task-1\nitem: task-1\n---\n## Summary\nBroken.\n",
        encoding="utf-8",
        newline="\n",
    )
    with _Ctx(store) as ctx:
        rows = query.results_for_item(ctx.index, "task-1")
        assert [r["record_id"] for r in rows] == [bad_id, good_id]
        selected = select_result(ctx, "task-1")
        assert selected.record["record_id"] == bad_id
        assert selected.current is False
        assert selected.completed is False


def test_result_with_nonexistent_candidate_commit_is_not_current(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    rid = _ulid_at("2026-11-15T10:00:00Z")
    _write_result_record(
        store,
        record_id=rid,
        attempt=1,
        at="2026-11-15T10:00:00Z",
        candidate_commit="c" * 40,
    )
    with _Ctx(store) as ctx:
        selected = select_result(ctx, "task-1")
        assert selected.current is False
        assert selected.completed is False
        assert any("candidate" in r for r in selected.reasons)


def test_stub_ordering_survives_cache_delete_and_rebuild(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    _write_item(store, "task-1")
    old_id = _ulid_at("2026-11-15T10:00:00Z")
    _write_result_record(
        store,
        record_id=old_id,
        attempt=1,
        at="2026-11-15T10:00:00Z",
        outcome="completed",
    )
    bad_id = _ulid_at("2026-11-15T11:00:00Z")
    bad_path = store / "records" / "findings" / "sprint" / "task-1" / f"{bad_id}.md"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(
        "---\nkind: result.recorded\nabout: [task-1\n---\n## Summary\nBroken.\n",
        encoding="utf-8",
        newline="\n",
    )

    with _Ctx(store) as ctx:
        before_rows = query.results_for_item(ctx.index, "task-1")
        before_selected = select_result(ctx, "task-1")

    index_path(store).unlink()
    with _Ctx(store) as ctx:
        after_rows = query.results_for_item(ctx.index, "task-1")
        after_selected = select_result(ctx, "task-1")

    assert [r["record_id"] for r in before_rows] == [bad_id, old_id]
    assert after_rows == before_rows
    assert after_selected.current is False
    assert after_selected.completed is False
    assert after_selected.record == before_selected.record

