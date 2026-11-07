from __future__ import annotations

import threading
from pathlib import Path

from helpers import git

from context_rail.core.locate import head_sha
from context_rail.model.codegraph import MemoryGraph, upstream
from context_rail.store.codegraph import (
    cache_path,
    freshness,
    has_snapshot,
    last_event,
    latest_commit,
    open_graph,
    record_event,
    save_snapshot,
    snapshot_info,
)

from .conftest import SnapshotFactory


def test_a_saved_snapshot_reads_back_through_the_neighbor_protocol(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    snapshot = make_snapshot("c1", extra=6)

    save_snapshot(store, snapshot)
    graph = open_graph(store, "c1")

    assert graph is not None
    memory = MemoryGraph(snapshot)
    assert upstream(graph, ["a.py:f"], depth=2) == upstream(memory, ["a.py:f"], depth=2)
    assert graph.node("a.py:f") == memory.node("a.py:f")
    assert graph.by_file("a.py") == memory.by_file("a.py")
    assert graph.by_name("run") == memory.by_name("run")
    assert graph.outgoing("a.py") == memory.outgoing("a.py")
    graph.close()


def test_the_cache_lives_outside_the_repository_tree(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    repo = store.parent

    save_snapshot(store, make_snapshot("c1"))

    assert cache_path(store).is_file()
    assert (repo / ".git") in cache_path(store).parents
    assert git(repo, "status", "--porcelain").strip() == ""


def test_snapshot_info_reports_provider_and_counts(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    save_snapshot(store, make_snapshot("c1", extra=2))

    info = snapshot_info(store, "c1")

    assert info is not None
    assert info["provider"] == "builtin"
    assert info["nodes"] == 6
    assert info["edges"] == 3
    assert snapshot_info(store, "other") is None


def test_retention_keeps_three_commits_and_every_protected_base(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    for number in range(1, 6):
        save_snapshot(store, make_snapshot(f"c{number}"), protect={"c1"})

    kept = {commit for commit in ("c1", "c2", "c3", "c4", "c5") if has_snapshot(store, commit)}

    assert kept == {"c1", "c3", "c4", "c5"}
    assert latest_commit(store) == "c5"


def test_a_concurrent_save_never_exposes_half_a_commit(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    snapshot = make_snapshot("c1", extra=300)
    errors: list[Exception] = []

    def save() -> None:
        try:
            save_snapshot(store, snapshot)
        except Exception as exc:  # noqa: BLE001 - a thread must report, not die
            errors.append(exc)

    threads = [threading.Thread(target=save) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    info = snapshot_info(store, "c1")
    assert info is not None
    assert info["nodes"] == len(snapshot.nodes)
    assert info["edges"] == len(snapshot.edges)
    graph = open_graph(store, "c1")
    assert graph is not None
    assert len(upstream(graph, ["a.py:f"], depth=1, limit=1000)) == 300
    graph.close()


def test_freshness_compares_the_snapshot_with_head(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    repo = store.parent
    assert freshness(store)["state"] == "none"

    head = git(repo, "rev-parse", "HEAD").strip()
    save_snapshot(store, make_snapshot(head))
    assert freshness(store) == {"state": "fresh", "graph": head, "head": head}

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "commit", "-am", "next")
    moved = git(repo, "rev-parse", "HEAD").strip()
    assert freshness(store) == {"state": "stale", "graph": head, "head": moved}


def test_head_sha_agrees_with_git_for_loose_packed_and_linked_refs(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    store, wt_store = git_repo_with_worktree
    repo, worktree = store.parent, wt_store.parent

    assert head_sha(repo) == git(repo, "rev-parse", "HEAD").strip()
    assert head_sha(store) == git(repo, "rev-parse", "HEAD").strip()

    git(repo, "pack-refs", "--all")
    assert head_sha(repo) == git(repo, "rev-parse", "HEAD").strip()

    assert head_sha(worktree) == git(worktree, "rev-parse", "HEAD").strip()
    (worktree / "extra.txt").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "extra.txt")
    git(worktree, "commit", "-m", "wt")
    assert head_sha(worktree) == git(worktree, "rev-parse", "HEAD").strip()
    assert head_sha(worktree) != head_sha(repo)


def test_head_sha_follows_a_detached_head(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    store, _ = git_repo_with_worktree
    repo = store.parent
    git(repo, "checkout", "--detach")

    assert head_sha(repo) == git(repo, "rev-parse", "HEAD").strip()


def test_head_sha_outside_git_is_none(tmp_path: Path) -> None:
    assert head_sha(tmp_path) is None


def test_a_corrupt_cache_file_reads_as_no_snapshot(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    save_snapshot(store, make_snapshot("c1"))
    cache_path(store).write_bytes(b"this is not a database" * 100)

    assert open_graph(store, "c1") is None
    event = last_event(store)
    assert event is not None
    assert event["kind"] == "invalid"


def test_a_corrupt_cache_file_is_replaced_by_the_next_save(
    git_repo_with_worktree: tuple[Path, Path], make_snapshot: SnapshotFactory
) -> None:
    store, _ = git_repo_with_worktree
    save_snapshot(store, make_snapshot("c1"))
    cache_path(store).write_bytes(b"this is not a database" * 100)

    save_snapshot(store, make_snapshot("c2"))

    assert has_snapshot(store, "c2")
    assert not has_snapshot(store, "c1")


def test_events_keep_only_the_latest(git_repo_with_worktree: tuple[Path, Path]) -> None:
    store, _ = git_repo_with_worktree
    assert last_event(store) is None

    record_event(store, "fallback", {"configured": "graphify"})
    record_event(store, "built", {"commit": "c1"})

    event = last_event(store)
    assert event is not None
    assert event["kind"] == "built"
    assert event["detail"] == {"commit": "c1"}
