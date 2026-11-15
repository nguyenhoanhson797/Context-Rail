"""Tests for the narrow ``result`` writer tool, permissions, replay, and recovery."""

from __future__ import annotations

import multiprocessing
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    BUGFIX_PRODUCER_SECTIONS,
    DEFAULT_PRODUCER_SECTIONS,
    as_human,
    as_seat,
    init_pack_store,
    init_result_store,
    init_store,
    record_inventory,
    record_result_for_test,
    refusal,
    require_git_identity,
    result_body,
    result_pack_document,
    run_git,
    tree_bytes,
)

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import EXECUTION_TOOLS, Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.results import (
    RESULT_BODY_MAX_BYTES,
    RESULT_KIND,
    result_body_hash,
)
from context_rail.model.roles import SEES_PARTS, parse_roles
from context_rail.store.doc import parse_doc, record_digest, record_frontmatter
from context_rail.store.results import select_result
from context_rail.store.write import repair

_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_RECEIPT_KEYS = {
    "record",
    "path",
    "item",
    "attempt",
    "outcome",
    "candidate_commit",
    "body_hash",
    "replayed",
}


def _add_item(
    store: Path,
    preset: str,
    title: str,
    *,
    rel: list[str] | None = None,
    actor: str = "cto",
) -> str:
    args: dict[str, Any] = {"preset": preset, "title": title}
    if rel:
        args["rel"] = rel
    added = invoke("add", args, store=store, actor=actor)
    assert is_ok(added), added
    return str(added["value"]["slug"])


def _write_body(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _store_files(store: Path) -> dict[str, bytes]:
    return {
        rel: data
        for rel, data in tree_bytes(store).items()
        if not rel.startswith(".rail-local/")
    }


def _assert_unchanged(store: Path, before_tree: dict[str, bytes], before_inv: dict[str, tuple[str, str]]) -> None:
    assert _store_files(store) == before_tree
    assert record_inventory(store) == before_inv


def _read_result_file(store: Path, rel: str) -> tuple[dict[str, Any], str]:
    raw = (store / rel).read_bytes()
    doc = parse_doc(rel, raw)
    assert not doc.problems, doc.problems
    front = record_frontmatter(raw)
    assert front is not None
    return front, doc.body


def _context(store: Path, actor: str = "cto") -> Context:
    manifest = load_manifest(store)
    return Context(store=store, manifest=manifest, pack=load_pack(manifest.pack, store), actor=actor)


def test_discovery_result_does_not_require_approval_or_git(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj", approve=False)
    phase = _add_item(store, "phase", "Discovery Phase")
    task = _add_item(store, "task", "Map auth endpoints", rel=[f"part_of={phase}"])
    body_path = _write_body(tmp_path / "outside.md", result_body(DEFAULT_PRODUCER_SECTIONS))

    assert "result" not in EXECUTION_TOOLS
    response = invoke(
        "result",
        {
            "item": task,
            "body_file": str(body_path),
            "model": "test-model",
            "harness": "test-harness",
        },
        store=store,
        actor="implementer",
    )

    assert is_ok(response), response
    value = response["value"]
    assert set(value) == _RECEIPT_KEYS
    assert value["item"] == task
    assert value["attempt"] == 1
    assert value["outcome"] == "completed"
    assert value["candidate_commit"] is None
    assert value["replayed"] is False
    assert _ULID_RE.fullmatch(value["record"])
    rel = value["path"]
    assert rel.endswith(f"/{value['record']}.md")
    assert rel == f"records/findings/sprint/{task}/{value['record']}.md"

    front, body = _read_result_file(store, rel)
    assert front["kind"] == RESULT_KIND
    assert front["workflow_name"] == "sprint"
    assert front["title"] == f"{task} result 1"
    assert front["digest"] == record_digest(front)
    assert front["data"]["outcome"] == "completed"
    assert front["data"]["candidate_commit"] is None
    assert front["data"]["body_hash"] == value["body_hash"]
    assert result_body_hash(body) == value["body_hash"]


def test_unparented_container_blocked_and_partial_items_record_results(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    unparented_bug = _add_item(store, "bug", "Unparented Bug")
    container = _add_item(store, "sprint", "Sprint One")
    blocker = _add_item(store, "task", "Blocker", rel=[f"part_of={container}"])
    blocked_task = _add_item(
        store,
        "task",
        "Blocked Task",
        rel=[f"part_of={container}", f"depends_on={blocker}"],
    )

    bug_body = _write_body(tmp_path / "bug.md", result_body(BUGFIX_PRODUCER_SECTIONS))
    bug_res = invoke(
        "result",
        {"item": unparented_bug, "body_file": str(bug_body), "outcome": "partial"},
        store=store,
        actor="implementer",
    )
    assert is_ok(bug_res), bug_res
    assert bug_res["value"]["outcome"] == "partial"
    assert bug_res["value"]["path"].startswith(f"records/findings/bugfix/{unparented_bug}/")

    container_body = _write_body(tmp_path / "container.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    cont_res = invoke(
        "result",
        {"item": container, "body_file": str(container_body), "outcome": "completed"},
        store=store,
        actor="cto",
    )
    assert is_ok(cont_res), cont_res

    blocked_body = _write_body(tmp_path / "blocked.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    blk_res = invoke(
        "result",
        {"item": blocked_task, "body_file": str(blocked_body), "outcome": "blocked"},
        store=store,
        actor="implementer",
    )
    assert is_ok(blk_res), blk_res
    assert blk_res["value"]["outcome"] == "blocked"


def test_contract_less_workflow_refuses_with_adoption_hint(tmp_path: Path) -> None:
    store = init_store(tmp_path / "legacy")
    task = _add_item(store, "task", "Legacy Task")
    body_path = _write_body(tmp_path / "body.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    res = invoke(
        "result",
        {"item": task, "body_file": str(body_path)},
        store=store,
        actor="implementer",
    )

    assert not is_ok(res)
    assert res["error"]["code"] == "INVALID_STATE"
    assert "results" in res["error"]["message"] or "adopt" in res["error"]["message"].lower()
    _assert_unchanged(store, before_tree, before_inv)


def test_body_input_and_metadata_refusals_leave_store_unchanged(tmp_path: Path) -> None:
    doc = result_pack_document()
    doc["presets"]["finding"]["required"] = ["workflow_name", "channel"]
    doc["presets"]["finding"]["fields"] = {"channel": ["research", "code"]}
    store_root = tmp_path / "proj"
    templates = store_root / "context-rail" / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", ("Summary", "Findings", "Evidence", "Verdict")),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    store = init_pack_store(
        store_root,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=True,
    )
    task = _add_item(store, "task", "Metadata Task")

    good_body = _write_body(tmp_path / "good.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    blank_body = _write_body(tmp_path / "blank.md", "   \n\n")
    empty_sec = _write_body(
        tmp_path / "empty_sec.md",
        "## Summary\n\n## Findings\nOk\n\n## Evidence\nOk\n\n## Next steps\nOk\n",
    )
    oversize_body = _write_body(
        tmp_path / "oversize.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "x" * (RESULT_BODY_MAX_BYTES + 1)}),
    )
    inside_store = _write_body(store / "notes" / "inside.md", result_body(DEFAULT_PRODUCER_SECTIONS))

    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    cases: list[tuple[dict[str, Any], str]] = [
        ({"item": task, "body_file": str(tmp_path / "missing.md"), "set": ["channel=research"]}, "NOT_FOUND"),
        ({"item": task, "body_file": str(blank_body), "set": ["channel=research"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(empty_sec), "set": ["channel=research"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(oversize_body), "set": ["channel=research"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(inside_store), "set": ["channel=research"]}, "FORBIDDEN"),
        ({"item": task, "body_file": str(good_body), "outcome": "bogus", "set": ["channel=research"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=research", "channel=code"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=research", "workflow_name=other"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=research", "actor=human"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=research", "outcome=failed"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=research", "body_hash=abc"]}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body)}, "VALIDATION_FAILED"),
        ({"item": task, "body_file": str(good_body), "set": ["channel=invalid"]}, "VALIDATION_FAILED"),
    ]
    for args, expected_code in cases:
        res = invoke("result", args, store=store, actor="implementer")
        assert not is_ok(res), (args, res)
        assert res["error"]["code"] == expected_code, (args, res)
        _assert_unchanged(store, before_tree, before_inv)

    accepted = invoke(
        "result",
        {"item": task, "body_file": str(good_body), "set": ["channel=research"]},
        store=store,
        actor="implementer",
    )
    assert is_ok(accepted), accepted
    front, _ = _read_result_file(store, accepted["value"]["path"])
    assert front["channel"] == "research"
    assert front["workflow_name"] == "sprint"


def test_commit_binding_requires_items_candidate_and_same_producer(tmp_path: Path) -> None:
    require_git_identity()
    proj = tmp_path / "repo"
    proj.mkdir()
    run_git(proj, "init", "-b", "main")
    store = init_result_store(proj)
    (proj / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    run_git(proj, "add", "app.py")
    run_git(proj, "commit", "-m", "base")

    item_a = _add_item(store, "task", "Task A")
    item_b = _add_item(store, "task", "Task B")

    (proj / "app.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
    run_git(proj, "add", "app.py")
    run_git(proj, "commit", "-m", "work a")
    sha_a = run_git(proj, "rev-parse", "HEAD").stdout.strip()

    (proj / "other.py").write_text("y = 1\n", encoding="utf-8", newline="\n")
    run_git(proj, "add", "other.py")
    run_git(proj, "commit", "-m", "work b")
    sha_b = run_git(proj, "rev-parse", "HEAD").stdout.strip()

    assert is_ok(invoke("candidate", {"item": item_a, "commit": sha_a}, store=store, actor="implementer"))
    assert is_ok(invoke("candidate", {"item": item_b, "commit": sha_b}, store=store, actor="implementer"))

    body_path = _write_body(tmp_path / "code_result.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    wrong_item = invoke(
        "result",
        {"item": item_a, "body_file": str(body_path), "commit": sha_b},
        store=store,
        actor="implementer",
    )
    assert not is_ok(wrong_item)
    assert wrong_item["error"]["code"] == "INVALID_STATE"
    _assert_unchanged(store, before_tree, before_inv)

    wrong_actor = invoke(
        "result",
        {"item": item_a, "body_file": str(body_path), "commit": sha_a},
        store=store,
        actor="other-dev",
    )
    assert not is_ok(wrong_actor)
    assert wrong_actor["error"]["code"] == "PERMISSION_DENIED"
    _assert_unchanged(store, before_tree, before_inv)

    bound = invoke(
        "result",
        {"item": item_a, "body_file": str(body_path), "commit": sha_a[:10]},
        store=store,
        actor="implementer",
    )
    assert is_ok(bound), bound
    assert bound["value"]["candidate_commit"] == sha_a

    # Same-producer check also works for seat callers.
    # Unrecorded commits in git (like base) still refuse with INVALID_STATE.
    base_sha = run_git(proj, "rev-parse", "HEAD~2").stdout.strip()
    unrecorded = invoke(
        "result",
        {"item": item_a, "body_file": str(body_path), "commit": base_sha},
        store=store,
        actor="implementer",
    )
    assert not is_ok(unrecorded)
    assert unrecorded["error"]["code"] == "INVALID_STATE"
    assert unrecorded["error"]["details"]["reason"] == "no_candidate"


def test_key_replays_after_input_disappears(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Retry Task")
    body_path = _write_body(tmp_path / "retry.md", result_body(DEFAULT_PRODUCER_SECTIONS))

    first = invoke(
        "result",
        {"item": task, "body_file": str(body_path), "key": "idem-result-1"},
        store=store,
        actor="implementer",
    )
    assert is_ok(first), first
    body_path.unlink()

    replayed = invoke(
        "result",
        {"item": task, "body_file": str(body_path), "key": "idem-result-1"},
        store=store,
        actor="implementer",
    )
    assert is_ok(replayed), replayed
    assert replayed["value"]["record"] == first["value"]["record"]
    assert replayed["value"]["path"] == first["value"]["path"]
    assert replayed["value"]["body_hash"] == first["value"]["body_hash"]
    assert replayed["value"]["replayed"] is True


def test_same_timestamp_attempts_have_distinct_ulids_and_deterministic_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Same Stamp")
    body1 = _write_body(tmp_path / "b1.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Attempt 1"}))
    body2 = _write_body(tmp_path / "b2.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Attempt 2"}))

    import context_rail.store.write as write_mod

    monkeypatch.setattr(write_mod, "now_iso", lambda: "2026-11-15T12:00:00Z")
    first = invoke("result", {"item": task, "body_file": str(body1), "outcome": "partial"}, store=store, actor="implementer")
    second = invoke("result", {"item": task, "body_file": str(body2), "outcome": "completed"}, store=store, actor="implementer")

    assert is_ok(first) and is_ok(second)
    assert first["value"]["record"] != second["value"]["record"]
    assert second["value"]["attempt"] == 2

    ctx = _context(store)
    try:
        selected = select_result(ctx, task)
    finally:
        ctx.close()
    assert selected.record is not None
    assert selected.record["record_id"] == second["value"]["record"]
    assert selected.completed is True


def _concurrent_result_worker(
    store: str, item: str, body: str, actor: str, barrier: Any, results: Any
) -> None:
    results.put(("ready", actor))
    try:
        barrier.wait(timeout=60.0)
        res = invoke(
            "result",
            {"item": item, "body_file": body},
            store=Path(store),
            actor=actor,
        )
    except BaseException as exc:
        results.put(("failed", actor, f"{type(exc).__name__}: {exc}"))
        raise
    results.put(("result", actor, res))


def test_concurrent_result_calls_preserve_distinct_files_and_attempts(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Concurrent Task")
    n = 4
    bodies = [
        str(_write_body(tmp_path / f"c{i}.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": f"Worker {i}"})))
        for i in range(n)
    ]

    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    barrier = ctx.Barrier(n + 1)
    workers = [
        ctx.Process(
            target=_concurrent_result_worker,
            args=(str(store), task, bodies[i], f"dev-{i}", barrier, q),
        )
        for i in range(n)
    ]
    for w in workers:
        w.start()
    try:
        for _ in range(n):
            msg = q.get(timeout=60.0)
            assert msg[0] == "ready", msg
        barrier.wait(timeout=60.0)
        collected: list[dict[str, Any]] = []
        for _ in range(n):
            msg = q.get(timeout=60.0)
            assert msg[0] == "result", msg
            collected.append(msg[2])
    finally:
        for w in workers:
            w.join(timeout=30.0)
            if w.is_alive():
                w.terminate()
                w.join(timeout=10.0)

    assert all(is_ok(res) for res in collected), collected
    records = [res["value"]["record"] for res in collected]
    paths = [res["value"]["path"] for res in collected]
    attempts = sorted(res["value"]["attempt"] for res in collected)
    assert len(set(records)) == n
    assert len(set(paths)) == n
    assert attempts == [1, 2, 3, 4]
    assert all((store / p).is_file() for p in paths)


_CRASH_RESULT_CHILD = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])

from context_rail.gateway.gateway import invoke
from context_rail.store import write as write_mod


def crash_on_create(planned):
    os._exit(9)


write_mod._create_record = crash_on_create
invoke("result", {"item": sys.argv[2], "body_file": sys.argv[4], "key": "crash-key"}, store=Path(sys.argv[1]), actor="implementer")
'''


def test_crash_before_result_create_is_repairable_and_syncs_index(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Crash Task")
    body = _write_body(tmp_path / "crash.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    src_dir = str(Path(__file__).resolve().parents[2] / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = src_dir

    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_RESULT_CHILD, str(store), task, src_dir, str(body)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 9, proc.stderr

    doctor = invoke("doctor", {}, store=store, actor="cto")
    assert is_ok(doctor), doctor
    assert "intent_pending" in [issue["code"] for issue in doctor["value"]["issues"]]

    repaired = repair(store)
    assert len(repaired["completed"]) == 1
    assert repaired["unrecoverable"] == []
    assert invoke("doctor", {}, store=store, actor="cto")["value"]["counts"]["error"] == 0

    ctx = _context(store)
    try:
        ctx.index.sync()
        selected = select_result(ctx, task)
    finally:
        ctx.close()
    assert selected.completed is True
    assert selected.record is not None

    body.unlink()
    replayed = invoke(
        "result",
        {"item": task, "body_file": str(body), "key": "crash-key"},
        store=store,
        actor="implementer",
    )
    assert is_ok(replayed), replayed
    assert replayed["value"]["record"] == selected.record["record_id"]
    assert replayed["value"]["replayed"] is True


def test_role_validation_and_readable_but_unwritable_seat_refusal(tmp_path: Path) -> None:
    doc = result_pack_document()
    doc["operating"] = {
        "version": 2,
        "unit": "task",
        "hierarchy": [{"preset": "phase", "required": True}],
        "roles": {
            "lead": {
                "anchor": "project",
                "reach": {"read": ["project"], "write": ["project"], "code": {"read": ["**"], "write": []}},
                "can": {"add": ["phase", "task", "bug"], "result": ["phase", "task", "bug"], "grant": ["observer", "phase-lead"]},
                "sees": {"up": 0, "down": 1, "parts": ["title", "status", "results"]},
            },
            "phase-lead": {
                "anchor": {"level": "phase"},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": []}},
                "can": {"add": ["task"], "result": ["task"], "grant": ["observer"]},
                "sees": {"up": 1, "down": "all", "parts": ["title", "status", "results"]},
            },
            "observer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["project"], "write": ["anchor"], "code": {"read": ["**"], "write": []}},
                "can": {"result": ["task"]},
                "sees": {"up": 1, "parts": ["title", "body", "results"]},
            },
        },
    }
    store_root = tmp_path / "role_proj"
    templates = store_root / "context-rail" / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", ("Summary", "Findings", "Evidence", "Verdict")),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    store = init_pack_store(
        store_root,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    from helpers import _write_charter
    from helpers import approve_picture as _approve_pic
    _write_charter(store)
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        approved = _approve_pic(store, actor=None)
        assert is_ok(approved), approved
    assert "results" in SEES_PARTS

    pack = load_pack(doc["name"], store)
    bad_can = {
        "lead": {
            "anchor": "project",
            "reach": {"read": ["project"], "write": ["project"]},
            "can": {"result": ["finding"]},
        }
    }
    with pytest.raises(Exception, match="not an item preset"):
        parse_roles(bad_can, pack)

    bad_sees = {
        "lead": {
            "anchor": "project",
            "reach": {"read": ["project"]},
            "sees": {"parts": ["title", "nonexistent_part"]},
        }
    }
    with pytest.raises(Exception, match="unknown part"):
        parse_roles(bad_sees, pack)

    with as_human():
        p1 = invoke("add", {"preset": "phase", "title": "Phase 1"}, store=store, actor=None)["value"]["slug"]
        p2 = invoke("add", {"preset": "phase", "title": "Phase 2"}, store=store, actor=None)["value"]["slug"]
        t1 = invoke("add", {"preset": "task", "title": "Task 1", "rel": [f"part_of={p1}"]}, store=store, actor=None)["value"]["slug"]
        t2 = invoke("add", {"preset": "task", "title": "Task 2", "rel": [f"part_of={p2}"]}, store=store, actor=None)["value"]["slug"]
        assert is_ok(invoke("seat grant", {"seat": "obs-1", "role": "observer", "anchor": t1}, store=store, actor=None))

    shown_t2 = as_seat(store, "obs-1", "show", {"slug": t2})
    assert is_ok(shown_t2), shown_t2

    body_path = _write_body(tmp_path / "obs.md", result_body(DEFAULT_PRODUCER_SECTIONS))
    denied = as_seat(store, "obs-1", "result", {"item": t2, "body_file": str(body_path)})
    assert refusal(denied) == ("PERMISSION_DENIED", "write_out_of_reach")

    allowed = as_seat(store, "obs-1", "result", {"item": t1, "body_file": str(body_path)})
    assert is_ok(allowed), allowed


def test_record_result_for_test_helper_uses_external_body_and_gateway(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    bug = _add_item(store, "bug", "Helper Bug")

    receipt = record_result_for_test(store, bug, outcome="partial", key="helper-k")

    assert receipt["item"] == bug
    assert receipt["outcome"] == "partial"
    assert (store / receipt["path"]).is_file()
    assert receipt["path"].startswith(f"records/findings/bugfix/{bug}/")
