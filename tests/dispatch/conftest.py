"""Keep dispatch worktrees out of the shared temp folder after each test."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_dispatch_worktrees() -> Iterator[None]:
    """Remove only the worktree roots this test created."""
    parent = Path(tempfile.gettempdir()) / "context-rail-worktrees"
    before = set(parent.glob("*")) if parent.is_dir() else set()
    yield
    after = set(parent.glob("*")) if parent.is_dir() else set()
    for path in sorted(after - before):
        shutil.rmtree(path, ignore_errors=True)
