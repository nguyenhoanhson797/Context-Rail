"""Status additions: too-long Items, the approval state and the GitHub-free freshness."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _status(store: Path) -> dict:
    result = invoke("status", {}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_status_reports_the_approval_state_and_too_long_items(
    digest_store: SimpleNamespace,
) -> None:
    value = _status(digest_store.store)
    assert value["approval"]["state"] == "approved"
    assert digest_store.too_long in value["too_long"]
    assert value["omitted"]["too_long"] == 0


def test_status_never_runs_git_or_any_process(
    digest_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("status never executes anything")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    assert _status(digest_store.store)["approval"]["state"] == "approved"


def test_a_missing_git_index_makes_freshness_unknown_not_fresh(leaf_item: SimpleNamespace) -> None:
    (leaf_item.root / "fake-droid-work.txt").write_text("done\n", encoding="utf-8")
    verified = invoke("verify", {"target": "unit"}, store=leaf_item.store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True, verified
    assert _status(leaf_item.store)["check_states"][0]["freshness"] == "fresh"

    index = leaf_item.root / ".git" / "index"
    original = index.read_bytes()
    try:
        index.unlink()
        freshness = _status(leaf_item.store)["check_states"][0]["freshness"]
        assert freshness in ("unknown", "stale", "missing")
        assert freshness != "fresh"
        text = invoke("cto brief", {}, store=leaf_item.store, actor="cto")["value"]["text"]
        assert "unknown" in text or "stale" in text or "missing" in text
    finally:
        index.write_bytes(original)
