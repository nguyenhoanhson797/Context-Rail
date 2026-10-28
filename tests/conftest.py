"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from context_rail.model.manifest import DEFAULT_MANIFEST
from context_rail.model.pack import Pack, load_pack
from context_rail.store.index import Index
from helpers import run_git
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
