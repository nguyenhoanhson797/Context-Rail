"""The CTO brief: pinned section order, bounded text and no out-of-store reads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from context_rail.context import recipes
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import run_cli

SECTIONS = (
    "Approval",
    "Goal",
    "Charter",
    "Reports",
    "Executable",
    "Active",
    "Quality",
    "Reversible",
    "Conventions",
)


def _brief(store: Path, **params: object) -> dict:
    result = invoke("cto brief", params, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_section_priority_order(brief_store: Path) -> None:
    text = _brief(brief_store)["text"]
    positions = [text.index(title) for title in SECTIONS]
    assert positions == sorted(positions)


def test_brief_is_bounded_with_omissions(large_brief_store: Path) -> None:
    value = _brief(large_brief_store, max_chars=4000, item_limit=5)
    assert len(value["text"]) <= 4000
    assert set(value) == {"text", "estimated_tokens", "omitted", "approval", "state"}
    assert sum(value["omitted"].values()) > 0
    assert value["estimated_tokens"] == (len(value["text"]) + 3) // 4


def test_a_tiny_budget_keeps_the_blockers_and_reports_omissions(
    large_brief_store: Path,
) -> None:
    value = _brief(large_brief_store, max_chars=500, item_limit=2)
    assert len(value["text"]) <= 500
    assert value["estimated_tokens"] == (len(value["text"]) + 3) // 4
    assert isinstance(value["omitted"], dict)
    assert any(count for count in value["omitted"].values())
    assert "Approval" in value["text"]


def test_brief_shows_the_approved_picture_and_the_decision(brief_store: Path) -> None:
    value = _brief(brief_store)
    assert value["approval"]["state"] == "approved"
    assert value["state"]["counts"]["items"]["total"] > 0
    assert "cto-reversible" in value["text"]
    assert "login-page" in value["text"]
    assert "Ship invoicing" in value["text"]


def test_brief_runs_no_process(brief_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a brief never executes anything")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "check_output", refuse)
    assert is_ok(invoke("cto brief", {}, store=brief_store, actor="cto"))


def test_cli_nested_command_renders_the_brief(brief_store: Path) -> None:
    completed = run_cli(
        brief_store.parent, "cto", "brief", "--max-chars", "12000", store=brief_store
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)["value"]
    assert set(value) == {"text", "estimated_tokens", "omitted", "approval", "state"}
    assert len(value["text"]) <= 12000
    assert value["state"]["approval"] == "approved"


def test_brief_and_status_need_no_git_on_the_path(
    brief_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _git_free_path()
    assert shutil.which("git", path=path) is None
    monkeypatch.setenv("PATH", path)
    for command in (("cto", "brief"), ("status",)):
        completed = run_cli(brief_store.parent, *command, store=brief_store)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["ok"] is True


def _git_free_path() -> str:
    """The interpreter's own directory plus system directories that hold no git."""
    python_dir = Path(sys.executable).resolve().parent
    candidates = [python_dir, *(Path(part) for part in os.defpath.split(os.pathsep))]
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates.extend([system / "System32", system])
    kept: list[str] = []
    seen: set[str] = set()
    for directory in candidates:
        if not str(directory) or str(directory).lower() in seen:
            continue
        seen.add(str(directory).lower())
        if (directory / "git").exists() or (directory / "git.exe").exists():
            continue
        kept.append(str(directory))
    return os.pathsep.join(kept)


def test_every_trimmed_section_reports_an_omission(brief_store: Path) -> None:
    """A section whose lines were dropped must say how much it lost."""
    value = _brief(brief_store, max_chars=600)
    present = {title: title in value["text"] for title in SECTIONS}
    assert not all(present.values()), "the budget must force some trimming"
    for key, title in zip(recipes.SECTION_KEYS, SECTIONS):
        if present[title]:
            continue
        assert value["omitted"][key] >= 1


def test_brief_hashes_and_reads_only_store_files(
    brief_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[Path] = []
    original = Path.read_bytes

    def record(self: Path) -> bytes:
        read.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", record)
    assert is_ok(invoke("cto brief", {}, store=brief_store, actor="cto"))
    store = brief_store.resolve()
    assert read, "the approval hashes are computed from store files"
    outside = [path for path in read if store not in path.resolve().parents]
    assert outside == []
