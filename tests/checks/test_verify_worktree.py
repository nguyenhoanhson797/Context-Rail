"""``rail check run --worktree`` verifies any git worktree of the project."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import records_of, refusal, run_git

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


@pytest.fixture
def worktree(leaf_item: SimpleNamespace, tmp_path: Path) -> Path:
    """A second worktree of the project with one commit the checkout does not have."""
    path = tmp_path / "wt"
    run_git(leaf_item.root, "worktree", "add", str(path), "-b", "work")
    (path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    (path / "fake-droid-work.txt").write_text("work\n", encoding="utf-8", newline="\n")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-m", "work in a worktree")
    return path


def head(path: Path) -> str:
    return run_git(path, "rev-parse", "HEAD").stdout.strip()


def verify(leaf_item: SimpleNamespace, value: Any) -> dict[str, Any]:
    return invoke(
        "verify",
        {"target": leaf_item.check, "worktree": str(value)},
        store=leaf_item.store,
        actor="cto",
    )


def test_verify_accepts_git_worktree_of_the_project(
    leaf_item: SimpleNamespace, worktree: Path
) -> None:
    result = verify(leaf_item, worktree)

    assert is_ok(result), result
    assert result["value"]["passed"] is True
    verdicts = records_of(leaf_item.store, "check.verdict")
    assert verdicts[-1]["data"]["candidate_commit"] == head(worktree)


def test_verify_refuses_plain_directory(leaf_item: SimpleNamespace, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    result = verify(leaf_item, plain)

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert "git worktree" in result["error"]["message"]


def test_verify_refuses_a_worktree_of_another_repository(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    run_git(other, "init", "-b", "main")

    assert refusal(verify(leaf_item, other))[0] == "VALIDATION_FAILED"


def test_verify_refuses_a_relative_path(leaf_item: SimpleNamespace, worktree: Path) -> None:
    result = verify(leaf_item, "wt")

    assert refusal(result)[0] == "VALIDATION_FAILED"


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters and separators are Windows")
def test_verify_accepts_worktree_path_spelled_differently(
    leaf_item: SimpleNamespace, worktree: Path
) -> None:
    drive, rest = str(worktree)[0], str(worktree)[1:]
    swapped = (drive.lower() if drive.isupper() else drive.upper()) + rest
    spelled = swapped.replace("\\", "/")

    result = verify(leaf_item, spelled)

    assert is_ok(result), result
    assert result["value"]["passed"] is True


def test_done_refused_until_candidate_content_is_in_checkout(
    leaf_item: SimpleNamespace, worktree: Path
) -> None:
    store = leaf_item.store
    tip = head(worktree)
    recorded = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, "model": "model-a"},
        store=store,
        actor="dev",
    )
    assert is_ok(recorded), recorded
    assert is_ok(verify(leaf_item, worktree))
    reviewed = invoke(
        "review",
        {"item": leaf_item.slug, "commit": tip, "verdict": "approve", "model": "model-b"},
        store=store,
        actor="qa",
    )
    assert is_ok(reviewed), reviewed

    def move(status: str) -> dict[str, Any]:
        shown = invoke("show", {"slug": leaf_item.slug}, store=store)
        return invoke(
            "move",
            {
                "slug": leaf_item.slug,
                "status": status,
                "expect": shown["value"]["doc"]["etag"],
            },
            store=store,
            actor="cto",
        )

    assert is_ok(move("doing"))
    refused = move("done")

    assert refusal(refused)[0] == "BUSINESS_RULE_VIOLATED"
    assert leaf_item.check in refused["error"]["details"]["missing"]

    run_git(leaf_item.root, "merge", "--ff-only", "work")

    assert is_ok(move("done"))
