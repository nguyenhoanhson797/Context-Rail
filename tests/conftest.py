"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import DEFAULT_MANIFEST
from context_rail.model.pack import Pack, load_pack
from context_rail.store.doc import etag
from context_rail.store.index import Index
from helpers import run_git, write_record
from perf.gen import make_store

_STAMP = "2026-10-28T00:00:00Z"

_DUPLICATE_TASK = f"""---
primitive: item
preset: task
title: Duplicate X Task
status: todo
tags: [task]
created: '{_STAMP}'
updated: '{_STAMP}'
part_of: '[[g1]]'
---
# Duplicate X Task

Only this task body mentions dupxtask.
"""

_DUPLICATE_NOTE = f"""---
primitive: note
preset: note
title: Duplicate X Note
tags: []
created: '{_STAMP}'
updated: '{_STAMP}'
---
# Duplicate X Note

Only this note body mentions dupxnote.
"""


@pytest.fixture
def small_pack(tmp_path: Path) -> Pack:
    """The built-in ``small`` pack, loaded for an empty store."""
    return load_pack("small", tmp_path)


@pytest.fixture
def store_factory(tmp_path: Path) -> Callable[..., Path]:
    """A generated store under a fresh child of ``tmp_path`` on every call."""
    counter = itertools.count()

    def factory(docs: int = 0, seed: int = 0) -> Path:
        return make_store(tmp_path / f"store{next(counter)}", docs, seed)

    return factory


def _indexed(store: Path) -> SimpleNamespace:
    pack = load_pack("small", store)
    index = Index.open(store, pack)
    index.sync()
    return SimpleNamespace(store=store, ix=index, pack=pack)


@pytest.fixture
def indexed_store(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """The 200-document generated store, opened and synced."""
    opened = _indexed(make_store(tmp_path / "indexed", 200))
    try:
        yield opened
    finally:
        opened.ix.close()


@pytest.fixture
def seeded_runs_store(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """A small store with one Check and one full implementer run.

    The store holds the projected shapes the CTO reads: a Check doc, a passing
    verdict, a completed run with two attempts and a skill pin, its candidate
    evaluation, a report, a review and a human approval.
    """
    store = make_store(tmp_path / "seeded", 12)
    item = "login-page"
    candidate = "a" * 40
    check_slug = "unit"
    check_at = "2026-10-28T10:05:00Z"
    run_id = "01JRUN000000000000000001"
    verdict_id = "01JVERDICT000000000000001"
    evaluation_id = "01JEVALUATION00000000001"
    report_id = "01JREPORT0000000000000001"
    review_id = "01JREVIEW0000000000000001"
    approval_id = "01JAPPROVAL00000000000001"

    task = f"""---
primitive: item
preset: task
title: Login page
status: doing
tags: [task]
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
part_of: '[[g0]]'
---
# Login page

The login form accepts valid credentials.
"""
    task_path = store / "items" / "task" / f"{item}.md"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(task, encoding="utf-8", newline="\n")

    check = f"""---
primitive: check
schema: 1
mode: cmd
acceptance: true
verifies: '[[{item}]]'
cmd: [py, -m, pytest, -q]
inputs: ['src/**/login*.py']
---
# Login works

The login test suite passes.
"""
    check_path = store / "checks" / f"{check_slug}.md"
    check_path.parent.mkdir(parents=True, exist_ok=True)
    check_path.write_text(check, encoding="utf-8", newline="\n")
    check_etag = etag(check_path.read_bytes())

    write_record(
        store,
        verdict_id,
        "check.verdict",
        {
            "schema": 1,
            "check": check_slug,
            "check_etag": check_etag,
            "passed": True,
            "mode": "cmd",
            "input_blobs": {"src/login.py": "oid123"},
            "input_globs": ["src/**/login*.py"],
            "runner_digest": "sha256:verdict",
            "exit": 0,
            "evidence": [],
            "candidate_commit": candidate,
        },
        at=check_at,
        about=(check_slug, item),
    )
    attempts = [
        {
            "number": 1,
            "session_id": "s1",
            "start": "2026-10-28T10:00:00Z",
            "end": "2026-10-28T10:00:12Z",
            "duration_s": 12.0,
            "exit": 1,
            "timed_out": False,
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "status": "failed",
        },
        {
            "number": 2,
            "session_id": "s1",
            "start": "2026-10-28T10:00:12Z",
            "end": "2026-10-28T10:00:32Z",
            "duration_s": 20.0,
            "exit": 0,
            "timed_out": False,
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "status": "completed",
        },
    ]
    write_record(
        store,
        run_id,
        "run.completed",
        {
            "schema": 1,
            "dispatch_id": "dispatch-1",
            "role": "implementer",
            "actor": "cto",
            "profile": "implementer",
            "profile_version": 1,
            "profile_etag": "0123456789abcdef",
            "skills": [
                {
                    "identity": "workspace/skills/test-first.md",
                    "version": 1,
                    "etag": "abcdef0123456789",
                }
            ],
            "model": "custom:Deepseek-V4-flash-0",
            "item": item,
            "session_id": "s1",
            "started_at": "2026-10-28T10:00:00Z",
            "ended_at": "2026-10-28T10:00:32Z",
            "duration_s": 32.0,
            "exit": 0,
            "status": "completed",
            "attempts": attempts,
            "candidate_commit": None,
            "accepted": None,
            "verdicts": [verdict_id],
            "files_changed": ["src/login.py"],
            "commits_changed": [candidate],
            "usage": {"input_tokens": 2, "output_tokens": 4},
        },
        at="2026-10-28T10:00:32Z",
        about=(item,),
    )
    write_record(
        store,
        evaluation_id,
        "run.evaluated",
        {
            "schema": 1,
            "run": run_id,
            "candidate_commit": candidate,
            "accepted": True,
            "check_verdicts": [verdict_id],
            "review": review_id,
            "at": "2026-10-28T10:10:00Z",
        },
        at="2026-10-28T10:10:00Z",
        about=(item,),
    )
    write_record(
        store,
        report_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": "engine",
            "target": "parser",
            "item": item,
            "run": run_id,
            "nothing_to_report": False,
            "what_happened": "the parser dropped a token",
            "expected": "every token is kept",
            "observed": "one token is lost",
            "impact": "low",
            "evidence": [],
            "fingerprint": "engine|parser|every token is kept|one token is lost",
        },
        at="2026-10-28T10:11:00Z",
        about=(item,),
    )
    write_record(
        store,
        review_id,
        "review.verdict",
        {
            "schema": 1,
            "item": item,
            "implementation_run": run_id,
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": candidate,
            "verdict": "approve",
            "findings": [],
            "evidence": [run_id],
            "profile_version": 1,
        },
        at="2026-10-28T10:12:00Z",
        about=(item,),
    )
    write_record(
        store,
        approval_id,
        "approval.recorded",
        {
            "schema": 1,
            "target": "charter",
            "protected_hashes": {
                "rail.yaml": "a" * 64,
                "policies/charter.md": "b" * 64,
            },
            "confirmer": "validator",
            "confirmation": "APPROVE charter",
        },
        at="2026-10-28T10:20:00Z",
        actor="human",
        about=("charter",),
    )

    pack = load_pack("small", store)
    index = Index.open(store, pack)
    index.sync()
    try:
        yield SimpleNamespace(
            store=store,
            ix=index,
            pack=pack,
            item=item,
            check=check_slug,
            check_etag=check_etag,
            run=run_id,
            dispatch="dispatch-1",
            candidate=candidate,
            verdict=verdict_id,
            evaluation=evaluation_id,
            report=report_id,
            review=review_id,
            approval=approval_id,
        )
    finally:
        index.close()


@pytest.fixture
def store_with_duplicate(tmp_path: Path) -> Path:
    """A generated store plus a task and a note that share the slug ``x``."""
    store = make_store(tmp_path / "duplicate", 20)
    (store / "items/task/x.md").write_text(
        _DUPLICATE_TASK, encoding="utf-8", newline="\n"
    )
    (store / "notes/note/x.md").write_text(
        _DUPLICATE_NOTE, encoding="utf-8", newline="\n"
    )
    return store


@pytest.fixture
def indexed_store_with_duplicate(
    store_with_duplicate: Path,
) -> Iterator[SimpleNamespace]:
    """``store_with_duplicate`` opened and synced."""
    opened = _indexed(store_with_duplicate)
    try:
        yield opened
    finally:
        opened.ix.close()


@pytest.fixture
def store_with_links(tmp_path: Path) -> Path:
    """An initialized store holding goal ``auth`` and tasks t1-t3 linked to it."""
    created = invoke("init", {"dir": str(tmp_path)}, actor="human")
    assert is_ok(created), created
    store = Path(created["value"]["store"])
    goal = invoke("add", {"preset": "goal", "title": "auth"}, store=store, actor="cto")
    assert is_ok(goal), goal
    for name in ("t1", "t2", "t3"):
        task = invoke(
            "add",
            {"preset": "task", "title": name, "rel": ["part_of=auth"]},
            store=store,
            actor="cto",
        )
        assert is_ok(task), task
    return store


def _rail_yaml() -> str:
    return yaml.safe_dump(DEFAULT_MANIFEST, sort_keys=False)


@pytest.fixture
def git_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A repo and one linked worktree, each checkout holding a store."""
    repo = tmp_path / "repo"
    store = repo / "context-rail"
    store.mkdir(parents=True)
    (store / "rail.yaml").write_text(_rail_yaml(), encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    run_git(repo, "init")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "fixture")
    worktree = tmp_path / "wt"
    run_git(repo, "worktree", "add", str(worktree))
    wt_store = worktree / "context-rail"
    if not (wt_store / "rail.yaml").is_file():
        wt_store.mkdir(parents=True, exist_ok=True)
        (wt_store / "rail.yaml").write_text(_rail_yaml(), encoding="utf-8")
    return store, wt_store
