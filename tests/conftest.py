"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

import copy
import functools
import importlib
import itertools
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
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
from context_rail.store.location import local_dir
from helpers import (
    DEFAULT_FAKE_BEHAVIORS,
    FakeDroid,
    InjectedFault,
    advance_default_branch,
    approve_picture,
    init_store,
    item_etag,
    require_git_identity,
    run_git,
    serve_ui,
    write_record,
    write_run,
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


@pytest.fixture(autouse=True)
def isolated_worktree_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Every test owns its dispatch worktrees, so sessions cannot collide."""
    root = tmp_path / "context-rail-worktrees"
    monkeypatch.setenv("RAIL_WORKTREE_ROOT", str(root))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def basic_pack(tmp_path: Path) -> Pack:
    """The built-in ``basic`` pack, loaded for an empty store."""
    return load_pack("basic", tmp_path)


@pytest.fixture
def store_factory(tmp_path: Path) -> Callable[..., Path]:
    """A generated store under a fresh child of ``tmp_path`` on every call."""
    counter = itertools.count()

    def factory(docs: int = 0, seed: int = 0) -> Path:
        return make_store(tmp_path / f"store{next(counter)}", docs, seed)

    return factory


def _indexed(store: Path) -> SimpleNamespace:
    pack = load_pack("basic", store)
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

    pack = load_pack("basic", store)
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


_REPORT_STAMP = "2026-10-28T12:00:00Z"


def _report_data(**overrides: Any) -> dict[str, Any]:
    """One valid report payload, with overrides for the case under test."""
    payload: dict[str, Any] = {
        "schema": 1,
        "target_class": "item",
        "target": "t0",
        "item": "t0",
        "run": None,
        "nothing_to_report": False,
        "what_happened": "the login form signed a user in with a bad password",
        "expected": "a bad password is refused",
        "observed": "the form signed the user in",
        "impact": "medium",
        "evidence": ["01JVERDICT000000000000001"],
        "fingerprint": None,
    }
    payload.update(overrides)
    return payload


def _write_report_file(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def report_payload() -> Callable[..., dict[str, Any]]:
    """A factory for one valid report payload with overrides."""
    return _report_data


@pytest.fixture
def completed_run(tmp_path: Path) -> SimpleNamespace:
    """A store with one verified run and the report file that run owes."""
    store = make_store(tmp_path / "completed", 3)
    run = "01JRUN000000000000000101"
    write_run(store, run, item="t0", status="verified")
    report_file = _write_report_file(tmp_path / "rail-report.json", _report_data())
    return SimpleNamespace(store=store, item="t0", run=run, report_file=report_file)


def _seed_report(
    store: Path, report_id: str, **overrides: Any
) -> str:
    payload = _report_data(**overrides)
    write_record(store, report_id, "report.filed", payload, at=_REPORT_STAMP)
    return report_id


@pytest.fixture
def triage_store(tmp_path: Path) -> SimpleNamespace:
    """One nonempty report whose single run recorded no failure."""
    store = make_store(tmp_path / "triage-one", 3)
    run = "01JRUN000000000000000201"
    write_run(store, run, item="t0", status="verified", at=_REPORT_STAMP)
    report = _seed_report(
        store,
        "01JREPORT0000000000000201",
        run=run,
        fingerprint="item|t0|a bad password is refused|the form signed the user in",
    )
    return SimpleNamespace(store=store, item="t0", run=run, report=report)


@pytest.fixture
def triage_store_two_reports(tmp_path: Path) -> SimpleNamespace:
    """Two runs, one failed, whose reports share one exact fingerprint."""
    store = make_store(tmp_path / "triage-two", 3)
    fingerprint = "item|t0|a bad password is refused|the form signed the user in"
    good = "01JRUN000000000000000301"
    bad = "01JRUN000000000000000302"
    write_run(store, good, item="t0", status="verified", at=_REPORT_STAMP)
    write_run(store, bad, item="t0", status="failed", at=_REPORT_STAMP)
    first = _seed_report(
        store, "01JREPORT0000000000000301", run=good, fingerprint=fingerprint
    )
    second = _seed_report(
        store, "01JREPORT0000000000000302", run=bad, fingerprint=fingerprint
    )
    return SimpleNamespace(
        store=store,
        item="t0",
        runs=(good, bad),
        report_ids=(first, second),
        fingerprint=fingerprint,
    )


@pytest.fixture
def triage_store_human_target(tmp_path: Path, answers: Path) -> SimpleNamespace:
    """Two reports whose cluster asks for a change to the approved charter."""
    result = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    store = tmp_path / "context-rail"
    approved = approve_picture(store)
    assert is_ok(approved), approved
    fingerprint = "item|charter|the charter keeps the budget|the budget grew"
    runs = ("01JRUN000000000000000401", "01JRUN000000000000000402")
    for run in runs:
        write_run(store, run, item="charter", status="verified", at=_REPORT_STAMP)
    reports = (
        _seed_report(
            store,
            "01JREPORT0000000000000401",
            target="charter",
            item="charter",
            run=runs[0],
            fingerprint=fingerprint,
            what_happened="a worker grew the charter budget without approval",
            expected="the charter keeps the budget",
            observed="the budget grew",
        ),
        _seed_report(
            store,
            "01JREPORT0000000000000402",
            target="charter",
            item="charter",
            run=runs[1],
            fingerprint=fingerprint,
            what_happened="a worker grew the charter budget without approval",
            expected="the charter keeps the budget",
            observed="the budget grew",
        ),
    )
    return SimpleNamespace(
        store=store, item="charter", report_ids=reports, fingerprint=fingerprint
    )


@pytest.fixture
def triage_store_engine(tmp_path: Path) -> SimpleNamespace:
    """Two engine reports that share one fingerprint and bounded evidence."""
    store = make_store(tmp_path / "triage-engine", 3)
    fingerprint = "engine|parser|every token is kept|one token is lost"
    runs = ("01JRUN000000000000000501", "01JRUN000000000000000502")
    for run in runs:
        write_run(store, run, item="t0", status="verified", at=_REPORT_STAMP)
    reports = (
        _seed_report(
            store,
            "01JREPORT0000000000000501",
            target_class="engine",
            target="parser",
            run=runs[0],
            fingerprint=fingerprint,
            what_happened="the parser dropped a token",
            expected="every token is kept",
            observed="one token is lost",
            evidence=[f"evidence-{number}" for number in range(15)],
        ),
        _seed_report(
            store,
            "01JREPORT0000000000000502",
            target_class="engine",
            target="parser",
            run=runs[1],
            fingerprint=fingerprint,
            what_happened="the parser dropped a token",
            expected="every token is kept",
            observed="one token is lost",
            evidence=[f"evidence-{number}" for number in range(15, 30)],
        ),
    )
    return SimpleNamespace(
        store=store, item="t0", report_ids=reports, fingerprint=fingerprint
    )


@pytest.fixture
def triage_store_anonymous(tmp_path: Path) -> SimpleNamespace:
    """Two reports that carry no run id and share one exact fingerprint."""
    store = make_store(tmp_path / "triage-anonymous", 2)
    fingerprint = "item|t0|a bad password is refused|the form signed the user in"
    reports = (
        _seed_report(
            store, "01JREPORT0000000000000701", run=None, fingerprint=fingerprint
        ),
        _seed_report(
            store, "01JREPORT0000000000000702", run=None, fingerprint=fingerprint
        ),
    )
    return SimpleNamespace(
        store=store, item="t0", report_ids=reports, fingerprint=fingerprint
    )


@pytest.fixture
def triage_store_nothing(tmp_path: Path) -> SimpleNamespace:
    """One nothing-to-report record, filed by a verified run."""
    store = make_store(tmp_path / "triage-nothing", 3)
    run = "01JRUN000000000000000601"
    write_run(store, run, item="t0", status="verified", at=_REPORT_STAMP)
    report = _seed_report(
        store,
        "01JREPORT0000000000000601",
        run=run,
        nothing_to_report=True,
        what_happened="",
        expected="",
        observed="",
        evidence=[],
        fingerprint="item|t0||",
    )
    return SimpleNamespace(store=store, item="t0", run=run, report=report)


@pytest.fixture
def skill_store(tmp_path: Path) -> Path:
    """A store holding one shared skill Note at ``workspace/skills/test-first.md``."""
    store = make_store(tmp_path / "skill", 1)
    added = invoke(
        "add",
        {
            "preset": "skill",
            "title": "test-first",
            "set": ["version=1", "description=Write the failing test first"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    assert (store / "workspace" / "skills" / "test-first.md").is_file()
    return store


@pytest.fixture
def edited_block(skill_store: Path) -> Path:
    """The synced ``AGENTS.md`` whose managed block was then hand edited."""
    synced = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(synced), synced
    agents = skill_store.parent / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    agents.write_text(
        text.replace(
            "<!-- CONTEXT-RAIL-END -->",
            "A human typed this line.\n<!-- CONTEXT-RAIL-END -->",
        ),
        encoding="utf-8",
        newline="\n",
    )
    return agents


class _Interrupted(Exception):
    """A crash raised from the replace step, before any target is swapped in."""


@pytest.fixture
def crash_before_replace(skill_store: Path) -> None:
    """Leave one interrupted adapter commit: published intent and staged temps."""
    from context_rail.store import write as write_module

    original = write_module._replace

    def crash(tmp: Path, target: Path, rel: str) -> None:
        raise _Interrupted()

    write_module._replace = crash
    try:
        invoke("adapters sync", {}, store=skill_store, actor="cto")
    finally:
        write_module._replace = original
    intents = local_dir(skill_store) / "intents"
    assert intents.is_dir() and list(intents.glob("*.json")), "the crash never published an intent"
    assert not (skill_store.parent / "AGENTS.md").exists()


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
    "pack": "basic",
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
    return build_leaf_item(tmp_path / "project", answers)


@pytest.fixture
def rooted_item(tmp_path: Path, answers: Path) -> SimpleNamespace:
    """The leaf task in a checkout that is itself the store (``--root .``)."""
    return build_leaf_item(tmp_path / "rooted", answers, root=".")


def build_leaf_item(
    project: Path, answers: Path, *, root: str | None = None
) -> SimpleNamespace:
    """An approved checkout with one executable leaf task and a cmd Check.

    ``root`` selects the store's own folder inside the checkout; ``None`` keeps
    the default ``context-rail`` child, ``"."`` makes the checkout the store.
    """
    project = Path(project)
    project.mkdir(exist_ok=True)
    args: dict[str, Any] = {
        "dir": str(project),
        "answers_file": str(answers),
        "confirm": "APPROVE charter",
    }
    if root is not None:
        args["root"] = root
    result = invoke("init", args, actor="human")
    assert is_ok(result), result
    store = project if root == "." else project / "context-rail"
    approved = approve_picture(store)
    assert is_ok(approved), approved
    added = invoke(
        "add",
        {"preset": "task", "title": "Login page", "rel": ["part_of=send-invoice"]},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    spec = project.parent / f"{project.name}-unit.json"
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
def verified_run(
    leaf_item: SimpleNamespace,
    fake_droid: FakeDroid,
    droid_env: dict[str, str],
    tmp_path: Path,
) -> SimpleNamespace:
    """An approved checkout whose single implementer run verified one candidate.

    The candidate and both worktrees stay on disk, so a review can be launched
    against exactly the commit the implementer produced. A configured but empty
    bare remote sits beside the project, so a push can be proven not to happen.
    """
    dispatched = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": leaf_item.etag, "key": "fixture-verified"},
        store=leaf_item.store,
        actor="cto",
    )
    assert is_ok(dispatched), dispatched
    assert dispatched["value"]["status"] == "verified", dispatched
    shown = invoke("show", {"slug": leaf_item.slug}, store=leaf_item.store, actor="cto")
    assert is_ok(shown), shown
    worktree = Path(dispatched["value"]["worktree"])
    candidate = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(leaf_item.root, "remote", "add", "origin", str(remote))
    base = run_git(leaf_item.root, "rev-parse", "HEAD").stdout.strip()
    return SimpleNamespace(
        store=leaf_item.store,
        project=leaf_item.root,
        root=leaf_item.root,
        item=leaf_item.slug,
        slug=leaf_item.slug,
        etag=shown["value"]["doc"]["etag"],
        run=dispatched["value"]["run"],
        dispatch_id=dispatched["value"]["dispatch_id"],
        dispatch_worktree=worktree,
        dispatch_branch=dispatched["value"]["branch"],
        candidate=candidate,
        remote=remote,
        branch="main",
        base=base,
    )


@pytest.fixture
def rejected_run(
    verified_run: SimpleNamespace, fake_droid: FakeDroid
) -> SimpleNamespace:
    """The verified run after one separated reviewer rejected its candidate."""
    fake_droid.set_behaviors([{"mode": "review_reject", "review": {"findings": ["no"]}}])
    rejected = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "key": "fixture-rejected"},
        store=verified_run.store,
        actor="cto",
    )
    assert is_ok(rejected), rejected
    assert rejected["value"]["verdict"] == "reject", rejected
    return SimpleNamespace(
        **{**vars(verified_run), "etag": item_etag(verified_run)}
    )


@pytest.fixture
def conflicting_run(verified_run: SimpleNamespace) -> SimpleNamespace:
    """The verified candidate plus a human commit that touches the same lines.

    The candidate edited ``fake-droid-work.txt``, so moving the default branch
    with a human edit of that file makes the merge's rebase conflict.
    """
    head = advance_default_branch(verified_run, "fake-droid-work.txt")
    return SimpleNamespace(**{**vars(verified_run), "base": head})


@pytest.fixture
def push_run(verified_run: SimpleNamespace, fake_droid: FakeDroid) -> SimpleNamespace:
    """The verified candidate in a project whose approved charter allows pushes.

    The bare remote already holds the pre-merge default branch, so the pushed
    head proves a normal fast-forward.
    """
    charter = verified_run.store / "charter.md"
    text = charter.read_text(encoding="utf-8")
    assert "allow_push: false" in text
    charter.write_text(
        text.replace("allow_push: false", "allow_push: true"),
        encoding="utf-8",
        newline="\n",
    )
    approved = approve_picture(verified_run.store)
    assert is_ok(approved), approved
    run_git(verified_run.project, "push", "origin", verified_run.branch)
    return SimpleNamespace(**{**vars(verified_run), "etag": item_etag(verified_run)})


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


@pytest.fixture(scope="session", autouse=True)
def _test_git_identity(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Every git process gets an author identity, including on a bare runner.

    A temporary home carries a ``.gitconfig`` that keeps the developer's own
    global config when one exists and always sets a test identity. ``HOME`` and
    ``USERPROFILE`` are the allow-listed variables the runner hands to a git
    child, so the identity reaches both a test's direct git call and rail's own,
    and no test depends on the machine having a git author.
    """
    real = _real_global_gitconfig()
    home = tmp_path_factory.mktemp("git-home")
    include = f"[include]\n\tpath = {real}\n" if real else ""
    (home / ".gitconfig").write_text(
        include
        + "[user]\n\tname = Context Rail Tests\n\temail = tests@example.invalid\n",
        encoding="utf-8",
        newline="\n",
    )
    saved = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture
def global_excludes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """A real global git excludesfile, returned by a writer of ignore patterns.

    A temporary home carries a ``.gitconfig`` that includes the developer's own
    global config (so an identity and every other setting stay intact) and then
    points ``core.excludesfile`` at a temp ignore file. ``HOME`` and
    ``USERPROFILE`` are the allow-listed variables the runner hands to a git
    child, so the override reaches both a test's direct git call and rail's own.
    """
    real = _real_global_gitconfig()
    home = tmp_path / "global-home"
    home.mkdir(exist_ok=True)
    ignore = tmp_path / "global-ignore"
    include = f"[include]\n\tpath = {real}\n" if real else ""
    (home / ".gitconfig").write_text(
        include + f"[core]\n\texcludesfile = {ignore.as_posix()}\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def writer(*patterns: str) -> Path:
        ignore.write_text("".join(f"{pattern}\n" for pattern in patterns), encoding="utf-8")
        return ignore

    return writer


def _real_global_gitconfig() -> str | None:
    """The developer's real global gitconfig, so an identity survives the override."""
    for candidate in (
        Path(os.environ.get("USERPROFILE", "")) / ".gitconfig",
        Path(os.environ.get("HOME", "")) / ".gitconfig",
        Path(os.environ.get("HOME", "")) / ".config" / "git" / "config",
    ):
        if candidate.is_file():
            return candidate.as_posix()
    return None


@pytest.fixture
def pilot_repo(tmp_path: Path) -> SimpleNamespace:
    """The generated pilot project and its local bare remote.

    The generator refuses to build without a configured git identity; that is an
    environment failure, so the test skips instead of inventing one.
    """
    from helpers import make_pilot_repo
    from support.pilot_repo import PilotError

    try:
        return make_pilot_repo(tmp_path / "pilot-root")
    except PilotError as error:
        pytest.skip(f"the pilot generator needs a git identity: {error}")


_VIEW_STAMP = "2026-10-28T12:00:00Z"


def _hours_ago(hours: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(hours=hours)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_item_doc(store: Path, slug: str, title: str, status: str) -> Path:
    """Seed one plain Item file, as a hand-created document looks on disk."""
    path = Path(store) / "items" / "task" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "primitive: item\n"
        "preset: task\n"
        f"title: {title}\n"
        f"status: {status}\n"
        "tags: [task]\n"
        f"created: '{_VIEW_STAMP}'\n"
        f"updated: '{_VIEW_STAMP}'\n"
        "---\n"
        f"# {title}\n\n{title}.\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _write_report(
    store: Path,
    record_id: str,
    *,
    item: str,
    target_class: str,
    target: str,
    what_happened: str,
    at: str = _VIEW_STAMP,
    run: str | None = None,
) -> Path:
    """Seed one valid report Record about an Item."""
    return write_record(
        store,
        record_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": target_class,
            "target": target,
            "item": item,
            "run": run,
            "nothing_to_report": False,
            "what_happened": what_happened,
            "expected": "the declared behaviour holds",
            "observed": "it did not",
            "impact": "medium",
            "evidence": [],
            "fingerprint": f"{target_class}|{target}|the declared behaviour holds|it did not",
        },
        at=at,
        about=(item,),
    )


@pytest.fixture
def brief_store(approved_store: Path) -> Path:
    """An approved picture with a leaf Item, an untriaged report and a reversible decision."""
    store = approved_store
    for title in ("Login page", "Sign in"):
        added = invoke("add", {"preset": "task", "title": title}, store=store, actor="cto")
        assert is_ok(added), added
    linked = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": invoke("show", {"slug": "sign-in"}, store=store)["value"]["doc"]["etag"],
            "reason": "reuses the login parser",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(linked), linked
    _write_report(
        store,
        "01JVIEWREPORT00000000001",
        item="login-page",
        target_class="item",
        target="login-page",
        what_happened="the form rejects the demo account",
    )
    return store


@pytest.fixture
def large_brief_store(tmp_path: Path) -> Path:
    """A 120-document store with far more Items than one brief page shows."""
    return make_store(tmp_path / "brief-large", 120)


@pytest.fixture
def unknown_usage_run(tmp_path: Path) -> SimpleNamespace:
    """One implementer run whose attempt reported no usage at all."""
    store = make_store(tmp_path / "unknown-usage", 6)
    write_run(store, "01JUNKNOWNUSAGE0000000001", item="t0", usage={}, at=_VIEW_STAMP)
    return SimpleNamespace(store=store, item="t0")


@pytest.fixture
def tick_with_workers(tmp_path: Path) -> SimpleNamespace:
    """A CTO tick run beside one worker run, each with its own attempt usage."""
    store = make_store(tmp_path / "tick", 6)
    write_run(
        store,
        "01JWORKERRUN00000000000001",
        item="t0",
        dispatch="dispatch-worker",
        usage={"input_tokens": 7, "output_tokens": 5, "factory_credits": 1},
        at=_VIEW_STAMP,
    )
    write_run(
        store,
        "01JTICKRUN0000000000000001",
        item=None,
        role="cto",
        dispatch="tick-1",
        usage={"input_tokens": 3, "output_tokens": 2, "factory_credits": 1},
        at="2026-10-28T12:01:00Z",
    )
    return SimpleNamespace(store=store, worker_input=7, tick_input=3)


@pytest.fixture
def tick_holder() -> Callable[..., Any]:
    """Hold the CTO single-instance lock the way a live tick does.

    Returns a context manager over a store; the lock is taken from this
    process with the same non-blocking OS primitive a tick uses.
    """
    import contextlib

    from context_rail.store.lock import file_lock

    @contextlib.contextmanager
    def hold(store: Path) -> Any:
        path = local_dir(Path(store)) / "cto.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path, timeout=0):
            yield path

    return hold


@pytest.fixture
def digest_store(tmp_path: Path, picture_answers: dict[str, Any]) -> SimpleNamespace:
    """An approved picture with a stuck Item, an over-budget Item and a human wait.

    The charter sets ``stuck_hours`` to 1 and the Item budget to 60 seconds, so a
    three-hour-old activity Record and a 120-second implementer run both exceed it.
    """
    picture = picture_answers
    picture["charter"]["thresholds"]["stuck_hours"] = 1
    picture["charter"]["budgets"]["item"]["seconds"] = 60
    answers = _write_answers(tmp_path / "digest-answers.json", picture)
    started = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(started), started
    store = tmp_path / "context-rail"
    approved = approve_picture(store)
    assert is_ok(approved), approved

    stuck = "stuck-task"
    too_long = "long-task"
    _write_item_doc(store, stuck, "Stuck task", "doing")
    _write_item_doc(store, too_long, "Long task", "todo")
    write_record(
        store,
        "01JSTUCKACTIVITY0000000001",
        "item.updated",
        {"schema": 1, "item": stuck},
        at=_hours_ago(3),
        about=(stuck,),
    )
    # The run and the wait sit inside the rolling window a digest queries with
    # ``since = now - 24h``, so the fixture keeps working as real time moves on.
    write_run(
        store,
        "01JLONGRUN00000000000000001",
        item=too_long,
        dispatch="dispatch-long",
        duration=120.0,
        at=_hours_ago(1),
    )
    _write_report(
        store,
        "01JHUMANWAIT000000000000001",
        item=stuck,
        target_class="human-wait",
        target="payer copy",
        what_happened="the reminder copy needs a human decision",
        at=_hours_ago(1),
    )
    for title in ("Login page", "Sign in"):
        added = invoke("add", {"preset": "task", "title": title}, store=store, actor="cto")
        assert is_ok(added), added
    linked = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": invoke("show", {"slug": "sign-in"}, store=store)["value"]["doc"]["etag"],
            "reason": "reuses the login parser",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(linked), linked
    return SimpleNamespace(store=store, stuck=stuck, too_long=too_long)




@pytest.fixture
def inject_fault() -> Iterator[Callable[..., None]]:
    """Replace ``module.attr`` for one call, then restore it at teardown."""
    patched: list[tuple[Any, str, bool, Any]] = []

    def patch(
        module: str,
        attr: str,
        *,
        nth: int = 1,
        mode: str = "raise",
        value: object = None,
    ) -> None:
        if mode not in {"raise", "return"}:
            raise ValueError(f"unknown inject_fault mode {mode!r}")
        target = importlib.import_module(module)
        had = hasattr(target, attr)
        original = getattr(target, attr)
        calls = {"n": 0}

        @functools.wraps(original)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == nth:
                if mode == "raise":
                    raise InjectedFault(f"{module}.{attr} fault")
                return value
            return original(*args, **kwargs)

        setattr(target, attr, wrapper)
        patched.append((target, attr, had, original))

    yield patch
    for target, attr, had, original in reversed(patched):
        if had:
            setattr(target, attr, original)
        else:
            delattr(target, attr)


@pytest.fixture
def measure_store(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """A fresh small-pack store with an open, synced index for measurement."""
    store = init_store(tmp_path)
    pack = load_pack("basic", store)
    index = Index.open(store, pack)
    index.sync()
    try:
        yield SimpleNamespace(store=store, ix=index, pack=pack)
    finally:
        index.close()


class _FixedClock(str):
    """The fixed timestamp, carrying a resettable ULID counter."""

    def __new__(cls, value: str) -> "_FixedClock":
        instance = super().__new__(cls, value)
        instance.counter = 0
        return instance

    def reset(self) -> None:
        self.counter = 0

    def mark(self) -> int:
        return self.counter

    def seek(self, position: int) -> None:
        self.counter = position


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Freeze ``now_iso`` and make ``new_ulid`` deterministic and resettable."""
    import context_rail.core.util as util

    clock = _FixedClock("2026-10-28T12:00:00Z")
    original_now, original_ulid = util.now_iso, util.new_ulid
    patched: list[tuple[Any, str]] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("context_rail") or module is util:
            continue
        if getattr(module, "now_iso", None) is original_now:
            setattr(module, "now_iso", lambda: str(clock))
            patched.append((module, "now_iso"))
        if getattr(module, "new_ulid", None) is original_ulid:
            setattr(module, "new_ulid", lambda ms=None: _next_ulid(clock))
            patched.append((module, "new_ulid"))

    def fixed_now() -> str:
        return str(clock)

    def fixed_ulid(ms: int | None = None) -> str:
        return _next_ulid(clock)

    monkeypatch.setattr(util, "now_iso", fixed_now)
    monkeypatch.setattr(util, "new_ulid", fixed_ulid)
    try:
        yield clock
    finally:
        for name, module in list(sys.modules.items()):
            if not name.startswith("context_rail") or module is util:
                continue
            if getattr(module, "now_iso", None) is fixed_now:
                setattr(module, "now_iso", original_now)
            if getattr(module, "new_ulid", None) is fixed_ulid:
                setattr(module, "new_ulid", original_ulid)
        for module, attr in reversed(patched):
            setattr(module, attr, original_now if attr == "now_iso" else original_ulid)


def _next_ulid(clock: _FixedClock) -> str:
    value = f"01J{clock.counter:023d}"
    clock.counter += 1
    return value


@pytest.fixture
def sync_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch-only driver context with its manifest in the environment."""
    store = init_store(tmp_path)
    nonce = "n1"
    scratch = local_dir(store) / "sync" / "scratch" / nonce
    scratch.mkdir(parents=True, exist_ok=True)
    manifest = scratch / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema": 1, "store": str(store), "scratch": str(scratch), "nonce": nonce}
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("RAIL_SYNC_SCRATCH", str(manifest))
    return SimpleNamespace(store=store, scratch=scratch, manifest=manifest, nonce=nonce)


@pytest.fixture
def branch_fixture_repo(tmp_path: Path) -> SimpleNamespace:
    """A repository path carrying a space and a non-ASCII character."""
    root = tmp_path / "repo with space \u00fc"
    root.mkdir()
    run_git(root, "init")
    return SimpleNamespace(root=root, path_with_space=str(root))


@pytest.fixture
def two_clones(tmp_path: Path, picture_answers: dict[str, Any]) -> SimpleNamespace:
    """An approved, pushed store cloned twice from a bare remote."""
    require_git_identity()
    picture = copy.deepcopy(picture_answers)
    picture["charter"]["allow_push"] = True
    picture["charter"]["default_branch"] = "main"
    answers = _write_answers(tmp_path / "sync-answers.json", picture)
    root = tmp_path / "origin"
    root.mkdir()
    started = invoke(
        "init",
        {"dir": str(root), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(started), started
    store = root / "context-rail"
    approve_picture(store)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (root / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    run_git(root, "init", "-b", "main")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "picture")
    bare = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(bare))
    run_git(root, "remote", "add", "origin", str(bare))
    run_git(root, "push", "-u", "origin", "main")
    a = tmp_path / "a"
    b = tmp_path / "b"
    run_git(tmp_path, "clone", "-c", "core.autocrlf=false", "-b", "main", str(bare), str(a))
    run_git(tmp_path, "clone", "-c", "core.autocrlf=false", "-b", "main", str(bare), str(b))
    run_git(a, "config", "core.autocrlf", "false")
    run_git(b, "config", "core.autocrlf", "false")
    return SimpleNamespace(bare=bare, a=a, b=b, store_a=a / "context-rail", store_b=b / "context-rail")


def _write_legacy(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def legacy_v2_store(tmp_path: Path) -> Path:
    """A representative v2 tree with documents, links and two event forms."""
    root = tmp_path / "legacy-v2"
    root.mkdir()
    _write_legacy(
        root,
        "rail.json",
        '{"schema_version": 2, "project_id": "legacy-fixture", "overrides": {}}\n',
    )
    _write_legacy(
        root,
        "context/Goal.md",
        "---\ntype: goal\ntitle: Ship invoicing\nstatus: active\n"
        "created: 2026-01-01\nupdated: 2026-01-02\n---\n# Ship invoicing\n\nOne goal.\n",
    )
    _write_legacy(
        root,
        "context/scope/SC-01.md",
        "---\ntype: scope\nid: SC-01\ntitle: Invoicing core\nstatus: done\n"
        "created: 2026-01-01\n---\n# Invoicing core\n\nScope body.\n",
    )
    _write_legacy(
        root,
        "what/tasks/login.md",
        "---\ntype: task\ntitle: Login page\nstatus: doing\nsupports: ['[[SC-01]]']\n"
        "created: 2026-01-03\nupdated: 2026-01-04\n---\n# Login page\n\nBuild it. See [[SC-01]].\n",
    )
    _write_legacy(
        root,
        "what/requirements/req-1.md",
        "---\ntype: requirement\ntitle: Email login\nstatus: todo\nscope:\n  item: SC-01\n"
        "---\n# Email login\n\nRequirement body.\n",
    )
    _write_legacy(
        root,
        "when/phases/phase-1.md",
        "---\ntype: phase\ntitle: Build\nstatus: active\n---\n# Build\n\nPhase body.\n",
    )
    _write_legacy(
        root,
        "now/Current.md",
        "---\ntype: note\ntitle: Current\n---\n# Current\n\nNow.\n",
    )
    _write_legacy(
        root,
        "history/decisions/use-sqlite.md",
        "---\ntype: decision\ntitle: Use SQLite\nstatus: done\n---\n# Use SQLite\n\nBecause it ships.\n",
    )
    _write_legacy(
        root,
        "inbox/idea.md",
        "---\ntype: idea\ntitle: An idea\n---\n# An idea\n\nUnconfirmed.\n",
    )
    _write_legacy(
        root,
        "log/2026-01/01JLEGACYEVENT00000000001.json",
        '{"v": 1, "id": "01JLEGACYEVENT00000000001", "at": "2026-01-05T10:00:00.000Z", '
        '"actor": "a+cli", "kind": "task.status", "subject": "task:login", '
        '"data": {"from": "todo", "to": "doing"}}\n',
    )
    _write_legacy(
        root,
        "log.jsonl",
        '{"v": 1, "id": "01JLEGACYEVENT00000000002", "at": "2026-01-06T10:00:00.000Z", '
        '"actor": "a+cli", "kind": "check.tick", "subject": "task:login", "data": {"ok": true}}\n',
    )
    return root


@pytest.fixture
def legacy_v1_store(tmp_path: Path) -> Path:
    """A representative v1 numbered tree with its registry."""
    root = tmp_path / "legacy-v1"
    root.mkdir()
    _write_legacy(
        root,
        "01-docs/tasks/old-task.md",
        "---\ntype: task\ntitle: Old task\nstatus: todo\n---\n# Old task\n\nLegacy body.\n",
    )
    _write_legacy(
        root,
        "01-docs/decisions/old-decision.md",
        "---\ntype: decision\ntitle: Old decision\n---\n# Old decision\n\nDecided.\n",
    )
    _write_legacy(
        root,
        "02-wiki/Goal.md",
        "---\ntitle: Old goal\n---\n# Old goal\n\nThe goal.\n",
    )
    _write_legacy(
        root,
        "05-meta/context-rail.json",
        '{"core_types": ["task"], "known_types": ["task", "decision"]}\n',
    )
    return root


@pytest.fixture
def ui_server(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """A loopback UI bound to port 0, stopped at teardown."""
    with serve_ui(init_store(tmp_path)) as created:
        yield created
