from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from helpers import git

from context_rail.model.codegraph import Snapshot
from context_rail.runner.codegraph import builtin, ensure_snapshot, readiness
from context_rail.store.codegraph import has_snapshot, last_event, snapshot_info
from context_rail.store.write import WriteRefused

from .conftest import RepoFactory


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_a_cache_hit_builds_nothing(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "def f():\n    pass\n"})
    head = _head(repo)

    first = ensure_snapshot(repo, worktree=repo, commit=head, configured="builtin")
    second = ensure_snapshot(repo, worktree=repo, commit=head, configured="builtin")

    assert (first.built, first.provider, first.fallback, first.warning) == (
        True,
        "builtin",
        None,
        None,
    )
    assert (second.built, second.provider) == (False, "builtin")
    assert has_snapshot(repo, head)


def test_a_built_snapshot_is_recorded(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)

    ensure_snapshot(repo, worktree=repo, commit=head, configured="builtin")

    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "built"
    assert snapshot_info(repo, head)["provider"] == "builtin"  # type: ignore[index]


@pytest.mark.parametrize("provider", ["graphify", "gitnexus"])
def test_external_providers_fall_back_to_builtin(
    make_repo: RepoFactory, provider: str
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)

    ensured = ensure_snapshot(repo, worktree=repo, commit=head, configured=provider)

    assert ensured.provider == "builtin"
    assert ensured.fallback == provider
    assert ensured.built is True
    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "fallback"
    assert event["detail"]["configured"] == provider


def test_none_builds_nothing(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)

    ensured = ensure_snapshot(repo, worktree=repo, commit=head, configured="none")

    assert ensured.provider is None
    assert ensured.built is False
    assert ensured.fallback is None
    assert not has_snapshot(repo, head)
    assert last_event(repo) is None


def test_a_timeout_returns_a_warning_not_an_error(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)

    def slow(worktree: Path, commit: str, *, deadline: float) -> Snapshot:
        raise TimeoutError("too slow")

    monkeypatch.setattr(builtin, "extract", slow)

    ensured = ensure_snapshot(
        repo, worktree=repo, commit=head, configured="builtin", timeout_s=30.0
    )

    assert ensured.provider is None
    assert ensured.warning is not None
    assert "30 s" in ensured.warning
    assert not has_snapshot(repo, head)
    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "timeout"


def test_the_extraction_deadline_follows_the_timeout(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    seen: dict[str, float] = {}
    original = builtin.extract

    def spy(worktree: Path, commit: str, *, deadline: float) -> Snapshot:
        seen["left"] = deadline - time.monotonic()
        return original(worktree, commit, deadline=deadline)

    monkeypatch.setattr(builtin, "extract", spy)

    ensure_snapshot(repo, worktree=repo, commit=_head(repo), configured="builtin", timeout_s=7.0)

    assert 5.0 < seen["left"] <= 7.0


def test_a_crashing_extractor_dispatches_without_a_graph(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    def broken(worktree: Path, commit: str, *, deadline: float) -> Snapshot:
        raise RuntimeError("boom")

    monkeypatch.setattr(builtin, "extract", broken)

    ensured = ensure_snapshot(repo, worktree=repo, commit=_head(repo), configured="builtin")

    assert ensured.provider is None
    assert ensured.warning is not None and "boom" in ensured.warning
    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "unavailable"


def test_a_tree_changed_during_extraction_is_refused(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    original = builtin.extract

    def messy(worktree: Path, commit: str, *, deadline: float) -> Snapshot:
        (worktree / "graph-out.json").write_text("{}", encoding="utf-8")
        return original(worktree, commit, deadline=deadline)

    monkeypatch.setattr(builtin, "extract", messy)

    with pytest.raises(WriteRefused) as caught:
        ensure_snapshot(repo, worktree=repo, commit=head, configured="builtin")

    assert caught.value.details["reason"] == "code_graph_dirty_tree"
    assert "graph-out.json" in caught.value.message
    assert not has_snapshot(repo, head)
    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "dirty_tree"


def test_files_that_were_already_dirty_are_not_blamed_on_the_provider(
    make_repo: RepoFactory,
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    (repo / "scratch.txt").write_text("mine\n", encoding="utf-8")

    ensured = ensure_snapshot(repo, worktree=repo, commit=_head(repo), configured="builtin")

    assert ensured.provider == "builtin"


def test_readiness_reports_builtin_ready(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "def f():\n    pass\n"})

    found = readiness(repo, "builtin")

    assert found["ready"] is True
    assert found["provider"] == "builtin"
    assert found["used"] == "builtin"
    assert found["step"] is None
    assert found["line"].startswith("code graph: ready (builtin 1, ")


def test_readiness_shows_the_size_once_a_snapshot_exists(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "def f():\n    pass\n"})
    head = _head(repo)
    ensure_snapshot(repo, worktree=repo, commit=head, configured="builtin")

    found = readiness(repo, "builtin")

    assert found["line"] == f"code graph: ready (builtin 1, 2 nodes @{head[:7]})"


@pytest.mark.parametrize("provider", ["graphify", "gitnexus"])
def test_readiness_of_an_external_provider_names_the_fallback(
    make_repo: RepoFactory, provider: str
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    found: dict[str, Any] = readiness(repo, provider)

    assert found["ready"] is False
    assert found["provider"] == provider
    assert found["used"] == "builtin"
    assert found["step"] == "binary"
    assert found["line"].startswith("code graph: not ready: binary:")
    assert found["line"].endswith("(fallback: builtin)")


def test_readiness_when_off(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    found = readiness(repo, "none")

    assert found["ready"] is False
    assert found["provider"] == "none"
    assert found["used"] is None
    assert found["line"] == "code graph: off"
