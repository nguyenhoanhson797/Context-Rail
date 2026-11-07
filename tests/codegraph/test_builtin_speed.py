from __future__ import annotations

import time
from pathlib import Path

import pytest

from context_rail.runner.codegraph.builtin import extract

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
def test_this_repository_extracts_under_five_seconds() -> None:
    started = time.monotonic()
    snapshot = extract(REPO_ROOT, "head", deadline=started + 60)
    elapsed = time.monotonic() - started

    assert snapshot.files_parsed > 0
    assert any(node.kind == "method" for node in snapshot.nodes)
    assert elapsed < 5.0, f"builtin extraction took {elapsed:.1f}s"
