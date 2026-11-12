"""The project root is the git worktree that holds the canonical store."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.store.location import project_root
from context_rail.store.write import WriteRefused
from helpers import run_git


def test_project_root_is_the_worktree_holding_the_store(tmp_path: Path) -> None:
    run_git(tmp_path, "init", "-b", "main")
    store = tmp_path / "context-rail"
    store.mkdir()

    assert project_root(store).resolve() == tmp_path.resolve()


def test_project_root_refuses_a_store_outside_git(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()

    with pytest.raises(WriteRefused) as refused:
        project_root(store)

    assert refused.value.code == "INTEGRATION_FAILURE"
