"""Contract-enabled Markdown reviews and legacy candidate-bound reviews."""

from __future__ import annotations

import contextlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    _write_charter,
    approve_picture,
    as_human,
    as_seat,
    init_pack_store,
    init_result_store,
    record_inventory,
    record_result_for_test,
    refusal,
    require_git_identity,
    result_body,
    result_pack_document,
    run_cli,
    run_git,
    tree_bytes,
)

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.results import (
    RESULT_BODY_MAX_BYTES,
    parse_review,
    result_body_hash,
)
from context_rail.store import query
from context_rail.store.doc import parse_doc, record_digest, record_frontmatter
from context_rail.store.location import index_path
from context_rail.store.results import select_result


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


def _write_notes(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _store_files(store: Path) -> dict[str, bytes]:
    return {
        rel: data
        for rel, data in tree_bytes(store).items()
        if not rel.startswith(".rail-local/")
    }


def _assert_unchanged(
    store: Path, before_tree: dict[str, bytes], before_inv: dict[str, tuple[str, str]]
) -> None:
    assert _store_files(store) == before_tree
    assert record_inventory(store) == before_inv


@contextlib.contextmanager
def _context(store: Path, actor: str = "cto"):
    manifest = load_manifest(store)
    ctx = Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor=actor,
    )
    try:
        ctx.index.sync()
        yield ctx
    finally:
        ctx.index.close()


def _init_git_result_store(
    tmp_path: Path,
    *,
    different_model: bool = True,
    with_roles: bool = False,
) -> tuple[Path, Path]:
    require_git_identity()
    project = tmp_path / "repo"
    project.mkdir(parents=True, exist_ok=True)
    run_git(project, "init", "-b", "main")

    doc = result_pack_document()
    if with_roles:
        doc["operating"] = {
            "version": 2,
            "unit": "task",
            "hierarchy": [{"preset": "phase", "required": True}],
            "roles": {
                "lead": {
                    "anchor": "project",
                    "reach": {
                        "read": ["project"],
                        "write": ["project"],
                        "code": {"read": ["**"], "write": []},
                    },
                    "can": {
                        "add": ["phase", "task"],
                        "result": ["phase", "task"],
                        "review": ["phase", "task"],
                        "grant": ["implementer", "reviewer"],
                    },
                },
                "implementer": {
                    "anchor": {"item": ["task"]},
                    "reach": {
                        "read": ["anchor", {"up": 1}],
                        "write": ["anchor"],
                        "code": {"read": ["**"], "write": ["**"]},
                    },
                    "can": {"result": ["task"], "candidate": ["task"]},
                },
                "reviewer": {
                    "anchor": {"item": ["task"]},
                    "reach": {
                        "read": ["anchor", {"up": 1}],
                        "write": ["anchor"],
                        "code": {"read": ["**"], "write": []},
                    },
                    "independent_of": ["produce"],
                    "can": {"review": ["task"]},
                },
            },
        }
    store = project / "context-rail"
    templates = store / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    store = init_pack_store(
        project,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    _write_charter(store)
    charter_path = store / "charter.md"
    text = charter_path.read_text(encoding="utf-8")
    charter_path.write_text(
        text.replace(
            "default_branch: main\n",
            f"default_branch: main\nreviewer_different_model: {'true' if different_model else 'false'}\n",
        ),
        encoding="utf-8",
        newline="\n",
    )
    if with_roles:
        with as_human():
            invoke("reindex", {}, store=store, actor=None)
            approved = approve_picture(store, actor=None)
            assert is_ok(approved), approved
    else:
        invoke("reindex", {}, store=store, actor="human")
        approved = approve_picture(store, actor="human")
        assert is_ok(approved), approved

    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run_git(project, "add", "app.py")
    run_git(project, "commit", "-m", "base")
    return project, store


def _commit_code(project: Path, content: str, msg: str = "work") -> str:
    (project / "app.py").write_text(content, encoding="utf-8", newline="\n")
    run_git(project, "add", "app.py")
    run_git(project, "commit", "-m", msg)
    return run_git(project, "rev-parse", "HEAD").stdout.strip()


def test_commitless_review_binds_exact_producer_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_rail.runner import git as runner_git

    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Research authentication")
    produced = record_result_for_test(store, task, actor="implementer")

    def no_git(*args: object, **kwargs: object) -> None:
        raise AssertionError("commitless review must not invoke git")

    monkeypatch.setattr(subprocess, "run", no_git)
    monkeypatch.setattr(runner_git, "resolve_commit", no_git)

    notes_path = _write_notes(
        tmp_path / "review-notes.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    reviewed = invoke(
        "review",
        {
            "item": task,
            "verdict": "approve",
            "notes_file": str(notes_path),
            "model": "model-b",
            "harness": "claude-code",
        },
        store=store,
        actor="reviewer",
    )

    assert is_ok(reviewed), reviewed
    value = reviewed["value"]
    assert value["item"] == task
    assert value["commit"] is None
    assert value["result"] == produced["record"]
    assert value["verdict"] == "approve"
    assert value["path"] == f"records/assessments/sprint/{task}/{value['record']}.md"

    rev_file = store / value["path"]
    assert rev_file.is_file()
    raw = rev_file.read_bytes()
    doc = parse_doc(value["path"], raw)
    assert not doc.problems, doc.problems
    front = record_frontmatter(raw)
    assert front is not None
    assert front["kind"] == "review.verdict"
    assert front["workflow_name"] == "sprint"
    assert front["digest"] == record_digest(front)

    review_data = front["data"]
    assert review_data["candidate_commit"] is None
    assert review_data["result_record"] == produced["record"]
    assert review_data["result_body_hash"] == produced["body_hash"]
    assert review_data["body_hash"] == result_body_hash(doc.body)
    assert review_data["implementer_actor"] == "implementer"
    assert review_data["reviewer_actor"] == "reviewer"
    assert review_data["reviewer_model"] == "model-b"
    assert review_data["harness"] == "claude-code"
    with _context(store) as ctx:
        assert (
            review_data["review_contract_digest"]
            == ctx.model.results["sprint"].review.digest
        )
        # Only review-mode checks run; result-mode checks are never verified implicitly
        assert all(
            row["mode"] == "review"
            for row in query.check_states(ctx.index, task)
            if row["slug"] in value["checks_verified"]
        )


def test_review_notes_do_not_supersede_producer(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Research caching")
    produced = record_result_for_test(store, task, actor="implementer")
    notes_path = _write_notes(
        tmp_path / "review.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    reviewed = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes_path)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(reviewed), reviewed

    with _context(store) as ctx:
        selected = select_result(ctx, task)
        assert selected.current is True
        assert selected.completed is True
        assert selected.binding is not None
        assert selected.binding.record == produced["record"]
        assert query.result_count(ctx.index, task) == 1


def test_contract_review_refuses_bad_notes_and_stale_or_wrong_bindings(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Task one")
    other = _add_item(store, "task", "Task two")

    # Missing notes_file refuses
    missing_notes = invoke(
        "review",
        {"item": task, "verdict": "approve"},
        store=store,
        actor="reviewer",
    )
    assert refusal(missing_notes)[0] == "VALIDATION_FAILED"

    # Empty required section refuses
    empty_sec = _write_notes(
        tmp_path / "empty.md",
        result_body(DEFAULT_REVIEW_SECTIONS, values={"Verdict": "<!-- only comment -->"}),
    )
    record_result_for_test(store, task, actor="implementer")
    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    bad_sec = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(empty_sec)},
        store=store,
        actor="reviewer",
    )
    assert refusal(bad_sec)[0] == "VALIDATION_FAILED"
    _assert_unchanged(store, before_tree, before_inv)

    # Oversized notes (> 256 KiB) refuse without writes
    big_notes = _write_notes(
        tmp_path / "big.md",
        result_body(DEFAULT_REVIEW_SECTIONS) + ("x" * RESULT_BODY_MAX_BYTES),
    )
    too_big = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(big_notes)},
        store=store,
        actor="reviewer",
    )
    assert refusal(too_big)[0] == "VALIDATION_FAILED"
    _assert_unchanged(store, before_tree, before_inv)

    # Result belonging to another item refuses
    other_res = record_result_for_test(store, other, actor="implementer")
    good_notes = _write_notes(
        tmp_path / "good.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    before_tree = _store_files(store)
    before_inv = record_inventory(store)
    wrong_item = invoke(
        "review",
        {
            "item": task,
            "result": other_res["record"],
            "verdict": "approve",
            "notes_file": str(good_notes),
        },
        store=store,
        actor="reviewer",
    )
    assert refusal(wrong_item)[0] == "INVALID_STATE"
    _assert_unchanged(store, before_tree, before_inv)

    # Newer partial attempt makes older completed result outdated
    with _context(store) as ctx:
        old_res = query.results_for_item(ctx.index, task, limit=5)[-1]
    record_result_for_test(store, task, actor="implementer", outcome="partial")
    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    outdated = invoke(
        "review",
        {
            "item": task,
            "result": old_res["record_id"],
            "verdict": "approve",
            "notes_file": str(good_notes),
        },
        store=store,
        actor="reviewer",
    )
    assert refusal(outdated)[0] == "INVALID_STATE"
    _assert_unchanged(store, before_tree, before_inv)

    partial_latest = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(good_notes)},
        store=store,
        actor="reviewer",
    )
    assert refusal(partial_latest)[0] == "INVALID_STATE"
    _assert_unchanged(store, before_tree, before_inv)


def test_contract_review_enforces_separation_and_model_rule(tmp_path: Path) -> None:
    project, store = _init_git_result_store(tmp_path, different_model=True, with_roles=True)
    # Write a profile document for shared-prof before seat grant
    prof_path = store / "workspace" / "profiles" / "shared-prof" / "profile.md"
    prof_path.parent.mkdir(parents=True, exist_ok=True)
    prof_path.write_text(
        "---\nprimitive: actor\npreset: profile\ntitle: Shared\ntags: []\n"
        "schema: 1\nversion: 1\nrecipe: implementer\n"
        "budget: {max_chars: 12000, max_docs: 20}\n"
        "skills: []\n"
        "budgets: {attempts: 2, seconds: 3600, tokens: null, credits: null}\n"
        "---\n# Shared\n\nShared profile.\n",
        encoding="utf-8",
        newline="\n",
    )
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        phase = _add_item(store, "phase", "Phase 1", actor=None)
        task = _add_item(store, "task", "Code task", rel=[f"part_of={phase}"], actor=None)
        assert is_ok(
            invoke(
                "seat grant",
                {
                    "seat": "impl-1",
                    "role": "implementer",
                    "anchor": task,
                    "profile": "shared-prof",
                },
                store=store,
                actor=None,
            )
        )
        assert is_ok(
            invoke(
                "seat grant",
                {
                    "seat": "rev-same-prof",
                    "role": "reviewer",
                    "anchor": task,
                    "profile": "shared-prof",
                },
                store=store,
                actor=None,
            )
        )
        assert is_ok(
            invoke(
                "seat grant",
                {"seat": "rev-1", "role": "reviewer", "anchor": task},
                store=store,
                actor=None,
            )
        )

    sha1 = _commit_code(project, "VALUE = 2\n", "c1")
    assert is_ok(
        as_seat(
            store,
            "impl-1",
            "candidate",
            {"item": task, "commit": sha1, "model": "model-a", "profile": "shared-prof"},
        )
    )
    body_path = _write_notes(
        tmp_path / "prod-body.md", result_body(DEFAULT_PRODUCER_SECTIONS)
    )
    prod = as_seat(
        store,
        "impl-1",
        "result",
        {"item": task, "body_file": str(body_path), "commit": sha1, "model": "model-a"},
    )
    assert is_ok(prod), prod

    notes_path = _write_notes(
        tmp_path / "rev-notes.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    before_tree = _store_files(store)
    before_inv = record_inventory(store)

    # Same profile refused via independence
    same_prof = as_seat(
        store,
        "rev-same-prof",
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes_path), "model": "model-b"},
    )
    assert refusal(same_prof) == ("PERMISSION_DENIED", "independence_violation")
    _assert_unchanged(store, before_tree, before_inv)

    # Same model refused when charter requires different model
    same_model = as_seat(
        store,
        "rev-1",
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes_path), "model": "model-a"},
    )
    assert refusal(same_model) == ("PERMISSION_DENIED", "separation")
    _assert_unchanged(store, before_tree, before_inv)

    # New candidate commit invalidates old candidate's result for approval
    sha2 = _commit_code(project, "VALUE = 3\n", "c2")
    assert is_ok(
        as_seat(
            store,
            "impl-1",
            "candidate",
            {"item": task, "commit": sha2, "model": "model-a"},
        )
    )
    before_tree = _store_files(store)
    before_inv = record_inventory(store)
    stale_cand = as_seat(
        store,
        "rev-1",
        "review",
        {
            "item": task,
            "commit": sha1,
            "verdict": "approve",
            "notes_file": str(notes_path),
            "model": "model-b",
        },
    )
    assert refusal(stale_cand)[0] == "INVALID_STATE"
    _assert_unchanged(store, before_tree, before_inv)

    # Record result for sha2, then approve with different model
    prod2 = as_seat(
        store,
        "impl-1",
        "result",
        {"item": task, "body_file": str(body_path), "commit": sha2, "model": "model-a"},
    )
    assert is_ok(prod2), prod2

    ok_rev = as_seat(
        store,
        "rev-1",
        "review",
        {
            "item": task,
            "commit": sha2,
            "result": prod2["value"]["record"],
            "verdict": "approve",
            "notes_file": str(notes_path),
            "model": "model-b",
        },
    )
    assert is_ok(ok_rev), ok_rev
    assert ok_rev["value"]["commit"] == sha2
    assert ok_rev["value"]["result"] == prod2["value"]["record"]


def test_contract_review_reject_missing_evidence_and_rebuild(tmp_path: Path) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Unrecorded task")
    notes_path = _write_notes(
        tmp_path / "reject-notes.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )

    rejected = invoke(
        "review",
        {"item": task, "verdict": "reject", "notes_file": str(notes_path)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rejected), rejected
    val = rejected["value"]
    assert val["commit"] is None
    assert val["result"] is None
    assert val["verdict"] == "reject"

    # Rebuild SQLite cache from scratch and verify null-binding review survives
    index_path(store).unlink()
    with _context(store) as ctx:
        row = query.review_for_item(ctx.index, task)
        assert row is not None
        assert row["record_id"] == val["record"]
        assert row["candidate_commit"] is None
        assert row["result_record"] is None
        assert row["result_body_hash"] is None
        assert row["verdict"] == "reject"

    # parse_review refuses null candidate + null result when no review_contract_digest is present
    with pytest.raises(ModelError):
        parse_review(
            {
                "schema": 1,
                "item": task,
                "reviewer_actor": "reviewer",
                "candidate_commit": None,
                "verdict": "reject",
            }
        )


def test_coordinator_cannot_claim_candidate_producer_result_and_same_actor_refused(
    tmp_path: Path,
) -> None:
    project, store = _init_git_result_store(tmp_path, different_model=False, with_roles=False)
    task = _add_item(store, "task", "Coord task")
    sha = _commit_code(project, "VALUE = 2\n", "impl")
    assert is_ok(
        invoke("candidate", {"item": task, "commit": sha}, store=store, actor="impl-1")
    )

    # Coordinator attempts to record result bound to impl-1's candidate -> refused
    body_path = _write_notes(
        tmp_path / "coord.md", result_body(DEFAULT_PRODUCER_SECTIONS)
    )
    coord_res = invoke(
        "result",
        {"item": task, "body_file": str(body_path), "commit": sha},
        store=store,
        actor="cto",
    )
    assert refusal(coord_res) == ("PERMISSION_DENIED", "producer_mismatch")

    # Same actor as producer result cannot approve commitless result
    task2 = _add_item(store, "task", "Research two")
    record_result_for_test(store, task2, actor="impl-1")
    notes_path = _write_notes(
        tmp_path / "rev.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    same_actor = invoke(
        "review",
        {"item": task2, "verdict": "approve", "notes_file": str(notes_path)},
        store=store,
        actor="impl-1",
    )
    assert refusal(same_actor) == ("PERMISSION_DENIED", "separation")


def test_review_does_not_implicitly_run_result_mode_checks_and_cli_supports_flags(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "CLI review task")
    produced = record_result_for_test(store, task, actor="implementer")

    # Declare a review-mode check on task (and ensure no result-mode check verdicts exist)
    spec = tmp_path / "rev-check.json"
    spec.write_text(
        json.dumps({"schema": 1, "mode": "review", "acceptance": False}),
        encoding="utf-8",
    )
    assert is_ok(
        invoke(
            "check",
            {"slug": "peer-rev", "target": task, "spec_file": str(spec), "expect": "absent"},
            store=store,
            actor="cto",
        )
    )

    notes_path = _write_notes(
        tmp_path / "cli-notes.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    proc = run_cli(
        tmp_path,
        "--actor",
        "reviewer",
        "loop",
        "review",
        task,
        "--result",
        produced["record"],
        "--verdict",
        "approve",
        "--notes-file",
        str(notes_path),
        store=store,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    out = json.loads(proc.stdout)
    assert out["ok"] is True
    assert out["value"]["commit"] is None
    assert out["value"]["result"] == produced["record"]
    assert out["value"]["checks_verified"] == ["peer-rev"]


def test_contract_review_reject_broken_or_unhashed_producer_stub(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Broken stub task")
    stub_id = "01JBR0KENSTVB0000000000001"
    stub_path = store / "records" / "findings" / "sprint" / task / f"{stub_id}.md"
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    stub_path.write_text(
        f"---\nid: {stub_id}\nprimitive: record\npreset: finding\nkind: result.recorded\n"
        f"about: [{task}]\n"
        "---\nnot valid frontmatter or body: [\n",
        encoding="utf-8",
        newline="\n",
    )
    with _context(store) as ctx:
        rows = query.results_for_item(ctx.index, task)
        assert len(rows) == 1
        assert rows[0]["record_id"] == stub_id
        assert rows[0]["body_hash"] is None

    notes_path = _write_notes(
        tmp_path / "reject-stub.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )

    # Implicit reject treats unhashed/broken producer stub as missing/unbindable evidence
    implicit = invoke(
        "review",
        {"item": task, "verdict": "reject", "notes_file": str(notes_path)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(implicit), implicit
    assert implicit["value"]["commit"] is None
    assert implicit["value"]["result"] is None
    assert implicit["value"]["verdict"] == "reject"

    # Explicit --result naming an unhashed/broken stub refuses cleanly with INVALID_STATE
    explicit = invoke(
        "review",
        {
            "item": task,
            "result": stub_id,
            "verdict": "reject",
            "notes_file": str(notes_path),
        },
        store=store,
        actor="reviewer",
    )
    assert refusal(explicit)[0] == "INVALID_STATE"


def test_contract_review_set_fields_and_in_store_notes_refusal(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Set field task")
    record_result_for_test(store, task, actor="implementer")
    notes_path = _write_notes(
        tmp_path / "set-notes.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )

    # Custom metadata field via --set persists on the review record
    ok = invoke(
        "review",
        {
            "item": task,
            "verdict": "approve",
            "notes_file": str(notes_path),
            "set": ["confidence=high"],
        },
        store=store,
        actor="reviewer",
    )
    assert is_ok(ok), ok
    doc = parse_doc(ok["value"]["path"], (store / ok["value"]["path"]).read_bytes())
    assert doc.fields["confidence"] == "high"

    # Reserved engine-owned field via --set refuses
    reserved = invoke(
        "review",
        {
            "item": task,
            "verdict": "approve",
            "notes_file": str(notes_path),
            "set": ["body_hash=abc"],
        },
        store=store,
        actor="reviewer",
    )
    assert refusal(reserved)[0] == "VALIDATION_FAILED"

    # Notes file inside the store directory is FORBIDDEN
    inside_notes = _write_notes(
        store / "notes" / "inside.md", result_body(DEFAULT_REVIEW_SECTIONS)
    )
    inside = invoke(
        "review",
        {
            "item": task,
            "verdict": "approve",
            "notes_file": str(inside_notes),
        },
        store=store,
        actor="reviewer",
    )
    assert refusal(inside)[0] == "FORBIDDEN"

