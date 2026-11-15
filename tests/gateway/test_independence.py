"""Independence and code reach are judged over the steps records show."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import as_human, restrict_code_write, write_candidate, write_record

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.handlers.independence import (
    code_reach_violations,
    independence_refusal,
)
from context_rail.kernel.seats import LiveSeat
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack

SHA = "a" * 40


def context_for(store: Path) -> Context:
    manifest = load_manifest(store)
    context = Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )
    context.index.sync()
    return context


def write_review(store: Path, record_id: str, item: str, reviewer: str) -> None:
    write_record(
        store,
        record_id,
        "review.verdict",
        {
            "schema": 1,
            "item": item,
            "implementation_run": "run-1",
            "implementer_actor": "dev",
            "reviewer_actor": reviewer,
            "implementer_model": "model-a",
            "reviewer_model": "model-b",
            "candidate_commit": SHA,
            "verdict": "approve",
            "findings": [],
            "evidence": [],
            "profile_version": 1,
        },
        at="2026-11-12T11:00:00Z",
        actor=reviewer,
        about=(item,),
    )


@pytest.fixture
def task(role_store: SimpleNamespace) -> SimpleNamespace:
    with as_human():
        added = invoke(
            "add",
            {"preset": "task", "title": "Independent", "rel": [f"part_of={role_store.o1}"]},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(added), added
    return SimpleNamespace(store=role_store.store, item=added["value"]["slug"], roles=role_store)


def test_produce_overlap_by_seat_refused(task: SimpleNamespace) -> None:
    write_candidate(task.store, "01JIND0000000000000000001", task.item, SHA, seat="impl-1")

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("produce",),
        acting=("impl-1",),
    )

    assert reason is not None
    assert "produce" in reason and "impl-1" in reason and task.item in reason


def test_produce_overlap_by_actor_when_no_seat_was_recorded(task: SimpleNamespace) -> None:
    write_candidate(task.store, "01JIND0000000000000000001", task.item, SHA, actor="agent-a")

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("produce",),
        acting=("agent-a",),
    )

    assert reason is not None and "agent-a" in reason


def test_produce_overlap_by_profile_refused(task: SimpleNamespace) -> None:
    write_candidate(
        task.store,
        "01JIND0000000000000000001",
        task.item,
        SHA,
        seat="impl-1",
        profile="shared",
    )

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("produce",),
        acting=("rev-1", "shared"),
    )

    assert reason is not None and "shared" in reason and "rev-1" not in reason


def test_review_overlap_refused(task: SimpleNamespace) -> None:
    write_review(task.store, "01JIND0000000000000000002", task.item, "qa")

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("review",),
        acting=("qa",),
    )

    assert reason is not None
    assert "review" in reason and "qa" in reason


def test_grant_overlap_refused(role_store: SimpleNamespace) -> None:
    reason = independence_refusal(
        context_for(role_store.store),
        item=role_store.orders,
        step_kinds=("grant",),
        acting=(role_store.lead,),
    )

    assert reason is not None
    assert "grant" in reason and role_store.lead in reason


def test_no_overlap_returns_none(task: SimpleNamespace) -> None:
    write_candidate(
        task.store, "01JIND0000000000000000001", task.item, SHA, seat="impl-1", profile="shared"
    )
    write_review(task.store, "01JIND0000000000000000002", task.item, "qa")

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("produce", "review", "grant", "not-a-step"),
        acting=("someone-else", "other-profile"),
    )

    assert reason is None


def test_steps_on_other_items_are_ignored(task: SimpleNamespace) -> None:
    write_candidate(
        task.store, "01JIND0000000000000000001", task.roles.o2, SHA, seat="impl-1"
    )

    reason = independence_refusal(
        context_for(task.store),
        item=task.item,
        step_kinds=("produce",),
        acting=("impl-1",),
    )

    assert reason is None


def test_empty_acting_returns_none(task: SimpleNamespace) -> None:
    write_candidate(task.store, "01JIND0000000000000000001", task.item, SHA, seat="impl-1")
    context = context_for(task.store)

    for acting in ((), ("",)):
        assert (
            independence_refusal(
                context, item=task.item, step_kinds=("produce",), acting=acting
            )
            is None
        )


def _implementer(context: Context, anchor: str) -> tuple[LiveSeat, object]:
    seat = LiveSeat(
        seat="impl-1",
        role="implementer",
        anchor=anchor,
        profile=None,
        granted_by="lead-1",
        granted_at="2026-11-12T10:00:00Z",
    )
    return seat, context.pack.operating.roles["implementer"]


def test_code_reach_inside_is_clean(task: SimpleNamespace) -> None:
    restrict_code_write(task.store, ["src/orders/**"])
    context = context_for(task.store)
    seat, role = _implementer(context, task.item)

    assert code_reach_violations(
        context, seat=seat, role=role, files=["src/orders/ok.py"]
    ) == []
    assert code_reach_violations(context, seat=seat, role=role, files=[]) == []


def test_code_reach_lists_outside_files(task: SimpleNamespace) -> None:
    restrict_code_write(task.store, ["src/orders/**"])
    context = context_for(task.store)
    seat, role = _implementer(context, task.item)

    violations = code_reach_violations(
        context, seat=seat, role=role, files=["src/orders/ok.py", "src/other/bad.py"]
    )

    assert violations == ["src/other/bad.py"]


def test_code_reach_normalises_backslashes(task: SimpleNamespace) -> None:
    restrict_code_write(task.store, ["src/orders/**"])
    context = context_for(task.store)
    seat, role = _implementer(context, task.item)

    violations = code_reach_violations(
        context,
        seat=seat,
        role=role,
        files=["src\\orders\\ok.py", "src\\other\\bad.py"],
    )

    assert violations == ["src/other/bad.py"]


def test_code_reach_dotdot_and_drive_paths_never_match(task: SimpleNamespace) -> None:
    restrict_code_write(task.store, ["src/orders/**"])
    context = context_for(task.store)
    seat, role = _implementer(context, task.item)
    files = ["src/orders/../other/x.py", "C:/orders/x.py"]

    assert code_reach_violations(context, seat=seat, role=role, files=files) == files


def test_code_reach_unresolvable_anchor_fails_closed(task: SimpleNamespace) -> None:
    context = context_for(task.store)
    seat, role = _implementer(context, "no-such-item")

    violations = code_reach_violations(
        context, seat=seat, role=role, files=["src\\a.py", "b.py"]
    )

    assert violations == ["src/a.py", "b.py"]


def test_commitless_result_produce_independence_blocks_same_seat_and_profile(
    tmp_path: Path,
) -> None:
    from helpers import DEFAULT_PRODUCER_SECTIONS, init_result_store, result_body

    from context_rail.core.util import dump_frontmatter, new_ulid
    from context_rail.model.results import (
        RESULT_KIND,
        RESULT_SCHEMA,
        result_body_hash,
    )
    from context_rail.store.doc import record_digest
    from context_rail.store.results import item_content_digest

    store = init_result_store(tmp_path / "proj")
    added = invoke("add", {"preset": "task", "title": "Result Indep"}, store=store, actor="cto")
    assert is_ok(added), added
    item = added["value"]["slug"]

    body = result_body(DEFAULT_PRODUCER_SECTIONS)
    record_id = new_ulid()
    item_path = f"items/task/{item}.md"
    ctx_init = context_for(store)
    try:
        contract_digest = ctx_init.model.results["sprint"].produce.digest
    finally:
        ctx_init.index.close()
    payload = {
        "schema": RESULT_SCHEMA,
        "item": item,
        "attempt": 1,
        "actor": "impl-1",
        "outcome": "completed",
        "candidate_commit": None,
        "body_hash": result_body_hash(body),
        "item_digest": item_content_digest(store, item_path),
        "work_cycle": None,
        "contract_digest": contract_digest,
        "seat": "impl-1",
        "seat_role": "implementer",
        "profile": "shared-prof",
        "profile_version": 1,
    }
    front: dict[str, object] = {
        "id": record_id,
        "primitive": "record",
        "preset": "finding",
        "kind": RESULT_KIND,
        "at": "2026-11-12T10:00:00Z",
        "actor": "impl-1",
        "about": [item],
        "workflow_name": "sprint",
        "data": payload,
    }
    front["digest"] = record_digest(front)
    rec_path = store / "records" / "findings" / "sprint" / item / f"{record_id}.md"
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(
        f"---\n{dump_frontmatter(front)}---\n{body}",
        encoding="utf-8",
        newline="\n",
    )

    ctx = context_for(store)
    try:
        assert ctx.index.conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE item = ?", (item,)
        ).fetchone()[0] == 0
        assert ctx.index.conn.execute(
            "SELECT COUNT(*) FROM results WHERE item = ?", (item,)
        ).fetchone()[0] == 1
        by_seat = independence_refusal(
            ctx, item=item, step_kinds=("produce",), acting=("impl-1", "other-prof")
        )
        by_profile = independence_refusal(
            ctx, item=item, step_kinds=("produce",), acting=("rev-1", "shared-prof")
        )
        clean = independence_refusal(
            ctx, item=item, step_kinds=("produce",), acting=("rev-1", "rev-prof")
        )
    finally:
        ctx.index.close()

    assert by_seat is not None and "produce" in by_seat and "impl-1" in by_seat
    assert by_profile is not None and "produce" in by_profile and "shared-prof" in by_profile
    assert clean is None

