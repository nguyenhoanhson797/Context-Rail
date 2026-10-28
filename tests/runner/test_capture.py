"""Executing-side capture of the worker's changed files and commits."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.runner.git import capture_changes
from context_rail.store.write import WriteRefused
from helpers import run_git


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "context-rail").mkdir()
    (root / "context-rail" / "rail.yaml").write_text(
        "schema: 3\n", encoding="utf-8", newline="\n"
    )
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
    return root


def test_capture_lists_the_candidate_files_and_commits(tmp_path):
    root = _repo(tmp_path)
    base = run_git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(root, "commit", "-am", "change the app")
    (root / "context-rail" / "record.json").write_text(
        "{}\n", encoding="utf-8", newline="\n"
    )
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "store change")
    capture = capture_changes(
        worktree=root, confine_to=root, base_commit=base, store_paths=("context-rail",)
    )
    assert capture["files_changed"] == ["app.py"]
    assert capture["store_changed"] == ["context-rail/record.json"]
    assert len(capture["commits_changed"]) == 2
    assert capture["complete"] is True
    assert capture["omitted"] == 0 and capture["reason"] is None


def test_an_unchanged_candidate_has_no_changes(tmp_path):
    root = _repo(tmp_path)
    head = run_git(root, "rev-parse", "HEAD").stdout.strip()
    capture = capture_changes(
        worktree=root, confine_to=root, base_commit=head, store_paths=("context-rail",)
    )
    assert capture["files_changed"] == [] and capture["commits_changed"] == []
    assert capture["store_changed"] == [] and capture["complete"] is True


def test_capture_is_bounded_with_an_explicit_omission_count(tmp_path):
    root = _repo(tmp_path)
    base = run_git(root, "rev-parse", "HEAD").stdout.strip()
    for index in range(5):
        (root / f"part{index}.py").write_text("x\n", encoding="utf-8", newline="\n")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "five parts")
    capture = capture_changes(
        worktree=root, confine_to=root, base_commit=base, store_paths=("context-rail",), limit=2
    )
    assert len(capture["files_changed"]) == 2
    assert capture["complete"] is False and capture["reason"] == "bounded"
    assert capture["omitted"] == 3


def test_a_git_failure_is_a_process_error(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(WriteRefused) as refused:
        capture_changes(
            worktree=root,
            confine_to=root,
            base_commit="0" * 40,
            store_paths=("context-rail",),
        )
    assert refused.value.code == "PROCESS_ERROR"


def test_an_option_like_revision_is_refused_before_git_runs(tmp_path):
    root = _repo(tmp_path)
    for bad in ("--upload-pack=evil", "-x", "", "a b"):
        with pytest.raises(WriteRefused) as refused:
            capture_changes(
                worktree=root, confine_to=root, base_commit=bad, store_paths=()
            )
        assert refused.value.code == "VALIDATION_FAILED"
