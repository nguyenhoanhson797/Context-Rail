from __future__ import annotations

import sys
from pathlib import Path

import pytest

from context_rail.core.locate import local_dir
from context_rail.runner import codegraph
from context_rail.runner.codegraph.provider import (
    ExtractionError,
    command_for,
    node_version_ok,
    parse_version,
    version_between,
)
from context_rail.store.codegraph import (
    drop_readiness,
    has_snapshot,
    last_event,
    provider_dir,
    read_readiness,
    snapshot_info,
    write_readiness,
)

from .conftest import RepoFactory


def _head(repo: Path) -> str:
    from helpers import git

    return git(repo, "rev-parse", "HEAD").strip()


class _Adapter:
    def __init__(self, calls: list[str], *, kind: str = "graphify", fail: bool = False) -> None:
        self.calls = calls
        self.kind = kind
        self.fail = fail

    def extract(self, worktree: Path, commit: str, *, cache_dir: Path, timeout_s: float):
        self.calls.append(self.kind)
        if self.fail:
            raise ExtractionError("unavailable", f"{self.kind} is not installed")
        from context_rail.model.codegraph import Snapshot

        return Snapshot(
            provider=self.kind,
            provider_version="1",
            commit=commit,
            built_at="2026-11-08T00:00:00Z",
            calls=True,
            lines=True,
            files_listed=1,
            files_parsed=1,
            skipped=(),
            nodes=(),
            edges=(),
        )


def test_parse_version_reads_tool_and_node_output() -> None:
    assert parse_version("graphify 0.9.104") == (0, 9, 104)
    assert parse_version("1.6.0") == (1, 6, 0)
    assert parse_version("v26.5.1") == (26, 5, 1)
    assert parse_version("no version here") is None


def test_version_between_is_inclusive_then_exclusive() -> None:
    assert version_between("0.9.104", "0.9.100", "0.10")
    assert not version_between("0.9.99", "0.9.100", "0.10")
    assert not version_between("0.10.0", "0.9.100", "0.10")
    assert version_between("1.6.0", "1.6", "1.7")


def test_node_version_accepts_both_supported_lines() -> None:
    for value in ("22.18.0", "24.11.0", "26.5.1"):
        assert node_version_ok(value)
    for value in ("22.17.9", "23.9.0", "24.10.9"):
        assert not node_version_ok(value)


def test_provider_dir_is_under_the_local_directory(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    assert provider_dir(repo, "graphify").is_relative_to(local_dir(repo))


def test_a_python_override_runs_through_the_interpreter() -> None:
    assert command_for("tool.py") == [sys.executable, "tool.py"]
    assert command_for("tool.exe") == ["tool.exe"]


def test_an_external_success_is_saved(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    calls: list[str] = []
    monkeypatch.setattr(codegraph, "_load_adapter", lambda provider: _Adapter(calls))

    ensured = codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")

    assert (ensured.provider, ensured.built, ensured.fallback, ensured.warning) == (
        "graphify",
        True,
        None,
        None,
    )
    assert calls == ["graphify"]
    assert snapshot_info(repo, head)["provider"] == "graphify"  # type: ignore[index]


def test_an_external_failure_falls_back_and_records_the_reason(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    calls: list[str] = []
    monkeypatch.setattr(
        codegraph, "_load_adapter", lambda provider: _Adapter(calls, fail=True)
    )

    ensured = codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")

    assert (ensured.provider, ensured.fallback) == ("builtin", "graphify")
    assert "not installed" in (ensured.warning or "")
    event = last_event(repo)
    assert event is not None
    assert event["kind"] == "fallback"
    assert event["detail"]["configured"] == "graphify"
    assert "not installed" in event["detail"]["reason"]
    assert has_snapshot(repo, head)


def test_an_external_timeout_falls_back(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)

    class Slow(_Adapter):
        def extract(self, *args, **kwargs):
            raise TimeoutError("too slow")

    monkeypatch.setattr(codegraph, "_load_adapter", lambda provider: Slow([]))

    ensured = codegraph.ensure_snapshot(
        repo, worktree=repo, commit=head, configured="gitnexus", timeout_s=0.01
    )

    assert ensured.provider == "builtin"
    assert ensured.fallback == "gitnexus"
    assert "timed out" in (ensured.warning or "")


def test_a_builtin_fallback_survives_until_forced(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    calls: list[str] = []
    failing = _Adapter(calls, fail=True)
    monkeypatch.setattr(codegraph, "_load_adapter", lambda provider: failing)

    first = codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")
    second = codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")

    assert first.provider == second.provider == "builtin"
    assert second.fallback == "graphify"
    assert calls == ["graphify"]
    assert second.warning == first.warning

    working = _Adapter(calls)
    monkeypatch.setattr(codegraph, "_load_adapter", lambda provider: working)
    forced = codegraph.ensure_snapshot(
        repo, worktree=repo, commit=head, configured="graphify", force=True
    )

    assert forced.provider == "graphify"
    assert calls == ["graphify", "graphify"]


def test_a_snapshot_from_another_external_provider_is_rebuilt(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    calls: list[str] = []
    monkeypatch.setattr(
        codegraph, "_load_adapter", lambda provider: _Adapter(calls, kind="gitnexus")
    )
    codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="gitnexus")

    monkeypatch.setattr(
        codegraph, "_load_adapter", lambda provider: _Adapter(calls, kind="graphify")
    )
    ensured = codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")

    assert ensured.provider == "graphify"
    assert calls == ["gitnexus", "graphify"]


def test_a_snapshot_for_another_commit_is_not_a_hit(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    head = _head(repo)
    calls: list[str] = []
    monkeypatch.setattr(codegraph, "_load_adapter", lambda provider: _Adapter(calls))
    codegraph.ensure_snapshot(repo, worktree=repo, commit=head, configured="graphify")

    ensured = codegraph.ensure_snapshot(
        repo, worktree=repo, commit="c2", configured="graphify"
    )

    assert ensured.commit == "c2"
    assert calls == ["graphify", "graphify"]


def test_readiness_round_trips_and_is_per_provider(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    write_readiness(
        repo,
        {"provider": "graphify", "ready": True, "line": "graphify ready"},
    )
    write_readiness(
        repo,
        {"provider": "gitnexus", "ready": False, "line": "node missing"},
    )

    records = read_readiness(repo)
    assert records["graphify"]["ready"] is True
    assert records["gitnexus"]["ready"] is False

    drop_readiness(repo, "graphify")
    assert "graphify" not in read_readiness(repo)
    assert "gitnexus" in read_readiness(repo)
