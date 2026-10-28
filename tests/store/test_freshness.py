"""Check freshness from the index and file stats: never git, never a source hash."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.store import git_index
from context_rail.store.git_index import (
    IndexSnapshot,
    freshness,
    match_inputs,
    read_index,
    selected_stats,
)
from helpers import run_git


def _check(inputs: list[str], etag: str = "etag-current", mode: str = "cmd") -> dict:
    return {"mode": mode, "inputs": inputs, "check_etag": etag}


def _verdict(snapshot: IndexSnapshot, inputs: list[str], *, etag: str = "etag-current") -> dict:
    matched = match_inputs(snapshot, inputs)
    return {
        "passed": True,
        "check_etag": etag,
        "input_blobs": {path: snapshot.entries[path].oid for path in matched},
        "input_globs": list(inputs),
    }


def _freshness(
    worktree: Path, snapshot: IndexSnapshot, verdict: dict | None, check: dict
) -> str:
    matched = match_inputs(snapshot, check.get("inputs", ()))
    return freshness(verdict, check, snapshot, selected_stats(worktree, matched))


def _commit_repo(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "fixture")


def test_reads_launch_no_process_and_hash_no_source(
    git_index_repo: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    verdict = _verdict(snapshot, ["docs/**"])
    calls: list[str] = []

    def refuse(name: str) -> object:
        def _called(*args: object, **kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"a read called {name}")

        return _called

    monkeypatch.setattr(subprocess, "Popen", refuse("Popen"))
    monkeypatch.setattr(subprocess, "run", refuse("run"))
    monkeypatch.setattr(subprocess, "check_output", refuse("check_output"))
    monkeypatch.setattr("context_rail.runner.run.run", refuse("runner.run"))

    stats = selected_stats(git_index_repo.worktree, match_inputs(snapshot, ["docs/**"]))
    result = freshness(verdict, _check(["docs/**"]), snapshot, stats)

    assert result == "fresh"
    assert calls == []
    assert "hashlib" not in vars(git_index)


def test_a_dirty_tracked_input_is_stale(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    verdict = _verdict(snapshot, ["docs/readme.md"])
    check = _check(["docs/readme.md"])
    assert _freshness(git_index_repo.worktree, snapshot, verdict, check) == "fresh"
    (git_index_repo.worktree / "docs/readme.md").write_text("# changed\n", encoding="utf-8")
    assert _freshness(git_index_repo.worktree, snapshot, verdict, check) == "stale"


def test_untracked_and_unmatched_inputs_are_missing(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    (git_index_repo.worktree / "src/untracked.py").write_text("X = 1\n", encoding="utf-8")
    assert match_inputs(snapshot, ["src/untracked.py"]) == ()
    assert (
        _freshness(
            git_index_repo.worktree,
            snapshot,
            {"passed": True, "check_etag": "etag-current", "input_blobs": {}},
            _check(["src/untracked.py"]),
        )
        == "missing"
    )
    assert (
        _freshness(git_index_repo.worktree, snapshot, None, _check(["nope/**/*.py"]))
        == "missing"
    )


def test_a_removed_input_is_unknown(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    verdict = _verdict(snapshot, ["docs/readme.md"])
    (git_index_repo.worktree / "docs/readme.md").unlink()
    stats = selected_stats(git_index_repo.worktree, ["docs/readme.md"])
    assert stats == {"docs/readme.md": None}
    result = _freshness(
        git_index_repo.worktree, snapshot, verdict, _check(["docs/readme.md"])
    )
    assert result == "unknown"


def test_changed_definition_or_a_failed_verdict_is_stale(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    verdict = _verdict(snapshot, ["docs/**"])
    check = _check(["docs/**"])
    assert _freshness(git_index_repo.worktree, snapshot, verdict, check) == "fresh"
    assert (
        _freshness(git_index_repo.worktree, snapshot, verdict, _check(["docs/**"], etag="other"))
        == "stale"
    )
    failed = {**verdict, "passed": False}
    assert _freshness(git_index_repo.worktree, snapshot, failed, check) == "stale"
    recorded = verdict["input_blobs"]["docs/readme.md"]
    incomplete = {**verdict, "input_blobs": {"docs/readme.md": recorded}}
    assert _freshness(git_index_repo.worktree, snapshot, incomplete, check) == "stale"


def test_skip_worktree_input_is_unknown_and_a_clean_input_stays_fresh(
    git_index_repo: SimpleNamespace,
) -> None:
    if git_index_repo.version != 3:
        pytest.skip("covered by the v3 parameter")
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    skipped = git_index_repo.skip_worktree
    entry = snapshot.entries[skipped]
    skip_verdict = {
        "passed": True,
        "check_etag": "etag-current",
        "input_blobs": {skipped: entry.oid},
    }
    result = _freshness(git_index_repo.worktree, snapshot, skip_verdict, _check([skipped]))
    assert result == "unknown"
    docs = _check(["docs/**"])
    assert (
        _freshness(git_index_repo.worktree, snapshot, _verdict(snapshot, ["docs/**"]), docs)
        == "fresh"
    )


def test_unmerged_input_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "conflict"
    _commit_repo(root, {"src/a.py": "base\n"})
    run_git(root, "checkout", "-b", "other")
    (root / "src/a.py").write_text("other\n", encoding="utf-8")
    run_git(root, "commit", "-am", "other")
    run_git(root, "checkout", "-")
    (root / "src/a.py").write_text("main\n", encoding="utf-8")
    run_git(root, "commit", "-am", "main")
    merged = subprocess.run(
        ["git", "merge", "other"], cwd=root, capture_output=True, text=True
    )
    assert merged.returncode != 0, merged.stdout + merged.stderr
    snapshot = read_index(root / ".git", root)
    entry = snapshot.entries["src/a.py"]
    verdict = {"passed": True, "check_etag": "etag-current", "input_blobs": {"src/a.py": entry.oid}}
    assert _freshness(root, snapshot, verdict, _check(["src/a.py"])) == "unknown"


def test_racy_timestamps_never_prove_fresh(tmp_path: Path) -> None:
    root = tmp_path / "racy"
    _commit_repo(root, {"src/a.py": "aaaa\n"})
    snapshot = read_index(root / ".git", root)
    entry = snapshot.entries["src/a.py"]
    verdict = {"passed": True, "check_etag": "etag-current", "input_blobs": {"src/a.py": entry.oid}}
    check = _check(["src/a.py"])
    assert _freshness(root, snapshot, verdict, check) == "fresh"

    target = root / "src/a.py"
    target.write_text("bbbb\n", encoding="utf-8", newline="\n")
    recorded = entry.mtime_s * 1_000_000_000 + entry.mtime_ns
    os.utime(target, ns=(recorded + 250_000, recorded + 250_000))
    observed = target.stat()
    assert observed.st_size == entry.size
    assert observed.st_mtime_ns != recorded
    assert _freshness(root, snapshot, verdict, check) in {"unknown", "stale"}


def test_same_blobs_after_a_squash_stay_fresh_and_a_changed_blob_goes_stale(
    tmp_path: Path,
) -> None:
    root = tmp_path / "squash"
    _commit_repo(root, {"src/a.py": "VALUE = 1\n", "notes.txt": "one\n"})
    snapshot = read_index(root / ".git", root)
    verdict = _verdict(snapshot, ["src/a.py"])
    check = _check(["src/a.py"])
    assert _freshness(root, snapshot, verdict, check) == "fresh"

    (root / "notes.txt").write_text("two\n", encoding="utf-8")
    run_git(root, "commit", "-am", "two")
    (root / "notes.txt").write_text("three\n", encoding="utf-8")
    run_git(root, "commit", "-am", "three")
    run_git(root, "reset", "--soft", "HEAD~2")
    run_git(root, "commit", "-m", "squashed")
    squashed = read_index(root / ".git", root)
    assert squashed.entries["src/a.py"].oid == verdict["input_blobs"]["src/a.py"]
    assert _freshness(root, squashed, verdict, check) == "fresh"

    (root / "src/a.py").write_text("VALUE = 2\n", encoding="utf-8")
    run_git(root, "commit", "-am", "changed")
    changed = read_index(root / ".git", root)
    assert _freshness(root, changed, verdict, check) == "stale"


def test_an_invalid_declared_glob_degrades_to_unknown(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    verdict = {"passed": True, "check_etag": "etag-current", "input_blobs": {}}

    assert freshness(verdict, _check(["src/[z-a].py"]), snapshot, {}) == "unknown"
    assert (
        freshness(verdict, _check(["src/a.py", "src/[z-a].py"]), snapshot, {})
        == "unknown"
    )


def test_an_untrusted_snapshot_makes_every_check_unknown(git_index_repo: SimpleNamespace) -> None:
    index = git_index_repo.git_dir / "index"
    index.write_bytes(index.read_bytes()[:-30])
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is not None
    human = {"mode": "human", "check_etag": "e"}
    assert freshness({"passed": True, "check_etag": "e"}, human, snapshot, {}) == "unknown"
    assert freshness(None, _check(["docs/**"]), snapshot, {}) == "unknown"


def test_bound_modes_use_definition_candidate_and_evidence(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    human = {"mode": "human", "check_etag": "e"}
    verdict = {"passed": True, "check_etag": "e", "evidence": ["01J"]}
    assert freshness(verdict, human, snapshot, {}) == "fresh"
    assert freshness(verdict, {"mode": "human", "check_etag": "x"}, snapshot, {}) == "stale"
    assert freshness({**verdict, "passed": False}, human, snapshot, {}) == "stale"
    assert freshness(None, human, snapshot, {}) == "missing"

    review = {"mode": "review", "check_etag": "e", "candidate_commit": "abc123"}
    assert freshness({**verdict, "candidate_commit": "abc123"}, review, snapshot, {}) == "fresh"
    assert freshness({**verdict, "candidate_commit": "def456"}, review, snapshot, {}) == "stale"

    evidence = {"mode": "evidence", "check_etag": "e", "evidence": ["01J"]}
    assert freshness(verdict, evidence, snapshot, {}) == "fresh"
    assert freshness(verdict, {**evidence, "evidence": ["02K"]}, snapshot, {}) == "stale"


def test_sha256_repository_inputs_are_fresh(tmp_path: Path) -> None:
    root = tmp_path / "sha256"
    root.mkdir()
    created = subprocess.run(
        ["git", "init", "--object-format=sha256", str(root)], capture_output=True, text=True
    )
    if created.returncode != 0:
        pytest.skip("this git cannot create SHA-256 repositories")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "sha256")
    snapshot = read_index(root / ".git", root)
    assert snapshot.oid_width == 32
    verdict = _verdict(snapshot, ["src/a.py"])
    assert len(verdict["input_blobs"]["src/a.py"]) == 64
    assert _freshness(root, snapshot, verdict, _check(["src/a.py"])) == "fresh"
