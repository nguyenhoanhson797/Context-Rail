"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

import copy
import itertools
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import DEFAULT_MANIFEST
from context_rail.model.pack import Pack, load_pack
from context_rail.model.policy import DECISION_RIGHTS, HUMAN_ONLY_IDS
from context_rail.store.doc import etag
from context_rail.store.index import Index
from helpers import (
    DEFAULT_FAKE_BEHAVIORS,
    FakeDroid,
    approve_picture,
    run_git,
    write_record,
)
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


_INDEX_VERSIONS = {"v2": 2, "v3": 3, "v4": 4}

_INDEX_FILES: dict[str, str] = {
    "README.md": "# fixture\n",
    "data/notes.txt": "notes\n",
    "docs/guide/intro.md": "# intro\n",
    "docs/readme.md": "# docs\n",
    "pkg/deep/nested/module.py": "MODULE = 1\n",
    "scripts/run.py": "print('run')\n",
    "src/main.py": "VALUE = 1\n",
    "src/pkg/a.py": "A = 1\n",
    "src/pkg/b.py": "B = 1\n",
    "tests/test_main.py": "def test_main():\n    assert True\n",
}


@pytest.fixture(params=["v2", "v3", "v4"])
def index_version(request: pytest.FixtureRequest) -> str:
    """The git index format a repository fixture writes."""
    return request.param


@pytest.fixture
def git_index_repo(tmp_path: Path, index_version: str) -> SimpleNamespace:
    """A committed repo whose index is written in ``index_version``'s format."""
    root = tmp_path / "index-repo"
    for rel, text in _INDEX_FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "fixture")
    version = _INDEX_VERSIONS[index_version]
    run_git(root, "update-index", f"--index-version={version}")
    skip = "src/pkg/a.py" if version == 3 else None
    if skip is not None:
        # git demotes a version 3 index to 2 unless an extended flag needs it.
        run_git(root, "update-index", "--skip-worktree", skip)
    written = int.from_bytes((root / ".git" / "index").read_bytes()[4:8], "big")
    assert written == version, f"git wrote index version {written}, not {version}"
    paths = tuple(run_git(root, "ls-files").stdout.splitlines())
    return SimpleNamespace(
        root=root,
        git_dir=root / ".git",
        worktree=root,
        paths=paths,
        version=version,
        skip_worktree=skip,
    )


# ── the reference onboarding picture ─────────────────────────────────────

PICTURE_ANSWERS: dict[str, Any] = {
    "schema": 1,
    "pack": "small",
    "goal": {
        "title": "Ship invoicing",
        "body": "Small teams send invoices without keeping a spreadsheet.",
    },
    "vision": {
        "title": "Invoices on autopilot",
        "body": "Every invoice goes out and gets paid without a human chasing it.",
    },
    "scope": [
        {
            "title": "Send invoice",
            "body": "Create an invoice and email it to the payer.",
            "priority": "must",
        },
        {
            "title": "Track payment",
            "body": "See which invoices are paid and which are not.",
            "priority": "should",
        },
        {
            "title": "Chase overdue",
            "body": "Send one reminder to a late payer.",
            "priority": "could",
        },
    ],
    "wont": [{"title": "Payroll", "reason": "A payroll product does it better."}],
    "charter": {
        "schema": 1,
        "project": {
            "goal": "ship-invoicing",
            "vision": "invoices-on-autopilot",
            "scope": ["send-invoice", "track-payment", "chase-overdue"],
            "wont": ["wont"],
        },
        "protected_files": [
            "rail.yaml",
            "charter.md",
            "project/ship-invoicing.md",
            "project/invoices-on-autopilot.md",
            "project/wont.md",
            "project/send-invoice.md",
            "project/track-payment.md",
        ],
        "gates": {
            "item_done": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": True,
                "require_human": False,
            },
            "scope_done": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": True,
                "require_human": False,
            },
            "merge": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": True,
                "require_human": False,
            },
            "release": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": True,
                "require_human": True,
            },
        },
        "human_in_loop": list(HUMAN_ONLY_IDS),
        "budgets": {
            "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
            "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
        },
        "thresholds": {
            "stuck_hours": 24,
            "near_budget_ratio": 0.8,
            "triage_reports": 2,
            "triage_weight": 2,
        },
        "change_rule": {"approval_required": True, "delegable": False},
        "decision_rights": {name: list(ids) for name, ids in DECISION_RIGHTS.items()},
        "default_branch": "main",
        "remote": "origin",
        "allow_push": False,
        "reviewer_different_model": True,
    },
}


def picture_answers_payload() -> dict[str, Any]:
    """A deep copy of the reference picture answers."""
    return copy.deepcopy(PICTURE_ANSWERS)


def _write_answers(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def picture_answers() -> dict[str, Any]:
    """The reference picture answers, ready to be adjusted by a test."""
    return picture_answers_payload()


@pytest.fixture
def answers(tmp_path: Path, picture_answers: dict[str, Any]) -> Path:
    """The reference answers file."""
    return _write_answers(tmp_path / "answers.json", picture_answers)


@pytest.fixture
def answers_with_extra_key(tmp_path: Path, picture_answers: dict[str, Any]) -> Path:
    """The reference answers file plus one unknown top-level key."""
    payload = {**picture_answers, "foo": 1}
    return _write_answers(tmp_path / "answers-extra.json", payload)


@pytest.fixture
def draft_file(tmp_path: Path, picture_answers: dict[str, Any]) -> Path:
    """The reference picture as a CTO draft file."""
    return _write_answers(tmp_path / "draft.json", picture_answers)


@pytest.fixture
def approved_store(tmp_path: Path, answers: Path) -> Path:
    """A store whose picture the human onboarded and approved."""
    result = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    store = tmp_path / "context-rail"
    approved = approve_picture(store)
    assert is_ok(approved), approved
    shown = invoke("show", {"slug": "charter"}, store=store, actor="human")["value"]
    assert shown["approval"]["state"] == "approved"
    return store


@pytest.fixture
def git_approved_store(tmp_path: Path, approved_store: Path) -> SimpleNamespace:
    """The approved picture committed in a git worktree with a ``Feature A`` task."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    added = invoke(
        "add", {"preset": "task", "title": "Feature A"}, store=approved_store, actor="cto"
    )
    assert is_ok(added), added
    run_git(tmp_path, "init")
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-m", "picture")
    return SimpleNamespace(store=approved_store, root=tmp_path, item="feature-a")


@pytest.fixture
def unapproved_store(tmp_path: Path, answers: Path) -> Path:
    """A store whose picture exists but the human has not approved yet."""
    result = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    return tmp_path / "context-rail"


#: The leaf task's cmd Check passes unless the worker leaves the marker ``bad``.
UNIT_COMMAND = (
    "import sys\n"
    "from pathlib import Path\n"
    "work = Path('fake-droid-work.txt')\n"
    "ok = work.is_file() and 'bad' not in work.read_text(encoding='utf-8')\n"
    "sys.exit(0 if ok else 1)\n"
)


@pytest.fixture
def leaf_item(tmp_path: Path, answers: Path) -> SimpleNamespace:
    """An approved git checkout with one executable leaf task and a cmd Check.

    The project lives in its own child folder so a test's other temporary
    files (the fake droid, for one) never appear as changes in the checkout.
    """
    project = tmp_path / "project"
    project.mkdir()
    result = invoke(
        "init",
        {"dir": str(project), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    store = project / "context-rail"
    approved = approve_picture(store)
    assert is_ok(approved), approved
    added = invoke(
        "add",
        {"preset": "task", "title": "Login page", "rel": ["part_of=send-invoice"]},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    spec = tmp_path / "unit.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "cmd",
                "acceptance": False,
                "cmd": [sys.executable, "-c", UNIT_COMMAND],
                "inputs": ["src/*.py"],
                "timeout_s": 60,
            }
        ),
        encoding="utf-8",
    )
    declared = invoke(
        "check",
        {
            "slug": "unit",
            "target": "login-page",
            "spec_file": str(spec),
            "expect": "absent",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(declared), declared
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (project / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    run_git(project, "init", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", "picture and leaf task")
    shown = invoke("show", {"slug": "login-page"}, store=store, actor="cto")
    assert is_ok(shown), shown
    return SimpleNamespace(
        store=store,
        root=project,
        slug="login-page",
        etag=shown["value"]["doc"]["etag"],
        check="unit",
    )


@pytest.fixture
def fake_droid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDroid:
    """The scripted fake droid, selected for the launcher through its env var."""
    holder = FakeDroid(tmp_path / "fake-droid", DEFAULT_FAKE_BEHAVIORS)
    monkeypatch.setenv("RAIL_DROID_BIN", str(holder.script))
    return holder


@pytest.fixture
def droid_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A parent environment with every inherited git and provider secret gone."""
    for name in list(os.environ):
        if name.startswith("GIT_") or name in ("GITHUB_TOKEN", "FACTORY_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FACTORY_API_KEY", "SECRET-TEST-7f3a")
    return dict(os.environ)


