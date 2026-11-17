"""Check freshness from the index and file stats: the reader never runs git or
hashes a source; a racy input may be content-verified by a caller-supplied bound."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.store import git_index
from context_rail.store.git_index import (
    IndexEntry,
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


def _racy_repo(tmp_path: Path, name: str) -> tuple[Path, IndexSnapshot, dict, dict]:
    """A committed repo whose index write time equals its input's mtime."""
    root = tmp_path / name
    _commit_repo(root, {"src/a.py": "aaaa\n"})
    snapshot = read_index(root / ".git", root)
    entry = snapshot.entries["src/a.py"]
    recorded = entry.mtime_s * 1_000_000_000 + entry.mtime_ns
    os.utime(root / ".git" / "index", ns=(recorded, recorded))
    git_index.clear_snapshot_cache()
    racy = read_index(root / ".git", root)
    assert racy.written_at == recorded
    verdict = {"passed": True, "check_etag": "etag-current", "input_blobs": {"src/a.py": entry.oid}}
    return root, racy, verdict, _check(["src/a.py"])


def test_a_racy_input_defers_to_the_content_verifier(tmp_path: Path) -> None:
    root, snapshot, verdict, check = _racy_repo(tmp_path, "racy-unit")
    stats = selected_stats(root, ["src/a.py"])
    assert freshness(verdict, check, snapshot, stats) == "unknown"
    assert freshness(verdict, check, snapshot, stats, verify_racy=lambda p, e: True) == "fresh"
    assert freshness(verdict, check, snapshot, stats, verify_racy=lambda p, e: False) == "stale"
    assert freshness(verdict, check, snapshot, stats, verify_racy=lambda p, e: None) == "unknown"


def test_freshness_of_verifies_a_racy_input_and_catches_a_same_tick_rewrite(
    tmp_path: Path,
) -> None:
    from context_rail.gateway.handlers.checks import freshness_of

    root, snapshot, verdict, check = _racy_repo(tmp_path, "racy-gate")
    entry = snapshot.entries["src/a.py"]
    recorded = entry.mtime_s * 1_000_000_000 + entry.mtime_ns
    row = {
        "mode": "cmd",
        "inputs": ["src/a.py"],
        "definition_etag": "etag-current",
        "passed": True,
        "verdict": verdict,
        "check_etag": "etag-current",
        "input_blobs": verdict["input_blobs"],
    }
    ctx = SimpleNamespace(store=tmp_path / "outside", manifest=None)
    assert freshness_of(ctx, row, worktree=root) == "fresh"

    target = root / "src/a.py"
    target.write_text("bbbb\n", encoding="utf-8", newline="\n")
    os.utime(target, ns=(recorded, recorded))
    assert target.stat().st_size == entry.size
    assert freshness_of(ctx, row, worktree=root) == "stale"


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

    undefined_candidate = {"mode": "review", "check_etag": "e"}
    assert (
        freshness({**verdict, "candidate_commit": "abc123"}, undefined_candidate, snapshot, {})
        == "fresh"
    )
    assert (
        freshness(
            {**verdict, "candidate_commit": "def456"}, undefined_candidate, snapshot, {}
        )
        == "fresh"
    )

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


def test_result_backed_review_check_freshness_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml
    from helpers import (
        DEFAULT_PRODUCER_SECTIONS,
        DEFAULT_REVIEW_SECTIONS,
        init_result_store,
        record_result_for_test,
        result_body,
    )

    from context_rail.core.result import is_ok
    from context_rail.gateway.gateway import Context, invoke
    from context_rail.gateway.handlers.checks import freshness_of
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack
    from context_rail.runner import git as runner_git
    from context_rail.runner import run as runner_run
    from context_rail.store import query

    store = init_result_store(tmp_path / "proj")
    added = invoke("add", {"preset": "task", "title": "Reviewed task"}, store=store, actor="cto")
    assert is_ok(added), added
    task = str(added["value"]["slug"])

    produced = record_result_for_test(store, task, actor="implementer", outcome="completed")
    notes = tmp_path / "review.md"
    notes.write_text(result_body(DEFAULT_REVIEW_SECTIONS), encoding="utf-8", newline="\n")
    rev = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rev), rev
    review_id = str(rev["value"]["record"])
    check_slug = str(rev["value"]["checks_verified"][0])

    def _check_freshness() -> str:
        manifest = load_manifest(store)
        pack = load_pack(manifest.pack, store)
        ctx = Context(store=store, manifest=manifest, pack=pack, actor="cto")
        try:
            row = query.check_state(ctx.index, check_slug)
            return freshness_of(ctx, row)
        finally:
            ctx.close()

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("freshness reads must not run git or subprocesses")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    monkeypatch.setattr(runner_git, "rev_parse_head", boom)
    monkeypatch.setattr(runner_run, "run", boom)

    assert _check_freshness() == "fresh"

    # 1. Editing producer result file makes review-mode check stale
    res_path = store / produced["path"]
    orig_res = res_path.read_bytes()
    res_path.write_text(
        res_path.read_text(encoding="utf-8") + "\nTampered.\n",
        encoding="utf-8",
        newline="\n",
    )
    assert _check_freshness() == "stale"
    res_path.write_bytes(orig_res)
    assert _check_freshness() == "fresh"

    # 2. Removing producer result file makes review-mode check stale
    res_path.unlink()
    assert _check_freshness() == "stale"
    res_path.write_bytes(orig_res)
    assert _check_freshness() == "fresh"

    # 3. Editing review file makes review-mode check stale
    rev_path = next((store / "records" / "assessments").rglob(f"{review_id}.md"))
    orig_rev = rev_path.read_bytes()
    rev_path.write_text(
        rev_path.read_text(encoding="utf-8") + "\nTampered review.\n",
        encoding="utf-8",
        newline="\n",
    )
    assert _check_freshness() == "stale"
    rev_path.unlink()
    assert _check_freshness() == "stale"
    rev_path.write_bytes(orig_rev)
    assert _check_freshness() == "fresh"

    # 4. Recording a newer partial (or completed) result supersedes the old review -> stale
    partial = record_result_for_test(store, task, actor="implementer", outcome="partial")
    assert _check_freshness() == "stale"
    (store / partial["path"]).unlink()
    assert _check_freshness() == "fresh"

    # 5. Recording a newer review supersedes the verdict -> stale until re-verified
    rev2 = invoke(
        "review",
        {"item": task, "verdict": "reject", "notes_file": str(notes)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rev2), rev2
    # Reject review failed verification -> stale
    assert _check_freshness() == "stale"

    # Re-approve so review-mode check is fresh again
    rev3 = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rev3), rev3
    assert _check_freshness() == "fresh"

    # 6. Changing Check definition etag makes it stale
    check_path = store / "checks" / f"{check_slug}.md"
    if not check_path.is_file():
        check_path = next((store / "checks").rglob(f"{check_slug}.md"))
    orig_check = check_path.read_bytes()
    check_path.write_text(
        orig_check.decode("utf-8").replace("acceptance: false", "acceptance: true"),
        encoding="utf-8",
        newline="\n",
    )
    assert _check_freshness() == "stale"
    check_path.write_bytes(orig_check)
    assert _check_freshness() == "fresh"

    # 7. Editing item content invalidates producer result -> review check becomes stale
    item_path = store / "items" / "task" / f"{task}.md"
    orig_item = item_path.read_bytes()
    item_path.write_text(
        orig_item.decode("utf-8") + "\nNew scope.\n",
        encoding="utf-8",
        newline="\n",
    )
    assert _check_freshness() == "stale"
    item_path.write_bytes(orig_item)
    assert _check_freshness() == "fresh"

    # 8. Contract drift makes review check stale
    pack_path = store / "packs" / "result-test" / "pack.yaml"
    orig_pack = pack_path.read_bytes()
    pack_doc = yaml.safe_load(orig_pack.decode("utf-8"))
    pack_doc["results"]["produce"]["sections"] = [*DEFAULT_PRODUCER_SECTIONS, "Risks"]
    pack_path.write_text(yaml.safe_dump(pack_doc, sort_keys=False), encoding="utf-8", newline="\n")
    assert _check_freshness() == "stale"
    pack_path.write_bytes(orig_pack)
    assert _check_freshness() == "fresh"

    # 9. Reopening the item (finished -> unfinished) changes work_cycle -> stale
    pack_doc = yaml.safe_load(orig_pack.decode("utf-8"))
    pack_doc["workflows"]["sprint"]["transitions"]["done"] = ["todo"]
    pack_path.write_text(yaml.safe_dump(pack_doc, sort_keys=False), encoding="utf-8", newline="\n")
    assert is_ok(invoke("approve", {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"}, store=store, actor="human"))
    shown = invoke("show", {"slug": task}, store=store)
    assert is_ok(invoke("move", {"slug": task, "status": "doing", "expect": shown["value"]["doc"]["etag"]}, store=store, actor="cto"))
    shown = invoke("show", {"slug": task}, store=store)
    assert is_ok(invoke("move", {"slug": task, "status": "done", "expect": shown["value"]["doc"]["etag"]}, store=store, actor="cto"))
    assert _check_freshness() == "fresh"
    shown = invoke("show", {"slug": task}, store=store)
    assert is_ok(invoke("move", {"slug": task, "status": "todo", "expect": shown["value"]["doc"]["etag"]}, store=store, actor="cto"))
    assert _check_freshness() == "stale"


def test_a_racy_crlf_checkout_matches_its_lf_blob(tmp_path: Path) -> None:
    import hashlib

    from context_rail.gateway.handlers.checks import _racy_content_verifier

    root = tmp_path / "crlf"
    (root / "src").mkdir(parents=True)
    target = root / "src/a.py"
    target.write_bytes(b"aaaa\r\n")
    blob = b"aaaa\n"
    oid = hashlib.sha1(b"blob %d\0" % len(blob) + blob).hexdigest()
    entry = IndexEntry("src/a.py", oid, 0o100644, len(b"aaaa\r\n"), 0, 0, 0, 0, None)
    snapshot = IndexSnapshot({}, 2, 20, None, None, None)
    verify = _racy_content_verifier(root, snapshot)

    assert verify("src/a.py", entry) is True
    target.write_bytes(b"bbbb\r\n")
    assert verify("src/a.py", entry) is False

