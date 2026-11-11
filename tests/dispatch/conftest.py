"""Shared dispatch fixtures: the per-test worktree root and a role-mode checkout."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import as_human, run_git

WORKER_SET = [
    "schema=1",
    "version=1",
    "recipe=implementer",
    "allowed_methods=[find, show, cto brief, ledger, report]",
    "budget={max_chars: 12000, max_docs: 20}",
    "model=gpt-5.3-codex",
    "tools=[read-cli, ls-cli, grep_tool_cli, glob-search-cli, create-cli, edit-cli, execute-cli]",
    "skills=[]",
    "budgets={attempts: 2, seconds: 3600, tokens: null, credits: null}",
]

UNIT_COMMAND = (
    "import sys\n"
    "from pathlib import Path\n"
    "work = Path('fake-droid-work.txt')\n"
    "ok = work.is_file() and 'bad' not in work.read_text(encoding='utf-8')\n"
    "sys.exit(0 if ok else 1)\n"
)


@pytest.fixture
def role_git_store(
    role_store: SimpleNamespace, tmp_path: Path, fake_droid: Any, droid_env: dict[str, str]
) -> SimpleNamespace:
    project = tmp_path
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "README.md").write_text("fixture\n", encoding="utf-8")

    body = project.parent / "worker-body.md"
    body.write_text("Implement one Item.\n", encoding="utf-8")
    with as_human():
        profile = invoke(
            "add",
            {
                "preset": "profile",
                "title": "worker",
                "set": WORKER_SET,
                "body_file": str(body),
                "confirm": "APPROVE worker",
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(profile), profile
        task = invoke(
            "add",
            {
                "preset": "task",
                "title": "Role task",
                "rel": [f"part_of={role_store.o1}"],
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(task), task
        spec = project.parent / "role-unit.json"
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
        check = invoke(
            "check",
            {
                "slug": "role-unit",
                "target": task["value"]["slug"],
                "spec_file": str(spec),
                "expect": "absent",
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(check), check

    run_git(project, "init", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", "role dispatch fixture")
    with as_human():
        shown = invoke("show", {"slug": task["value"]["slug"]}, store=role_store.store, actor=None)
    assert is_ok(shown), shown
    return SimpleNamespace(
        store=role_store.store,
        lead=role_store.lead,
        orders_seat=role_store.orders_seat,
        billing=role_store.billing,
        task=task["value"]["slug"],
        sibling=role_store.b1,
        root=project,
        etag=shown["value"]["doc"]["etag"],
        run=role_store.store,
    )
