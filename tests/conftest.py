"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from context_rail.model.manifest import DEFAULT_MANIFEST
from context_rail.model.pack import Pack, load_pack
from helpers import run_git
from perf.gen import make_store


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
