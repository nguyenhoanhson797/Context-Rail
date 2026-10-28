"""The git index reader: real v2, v3 and v4 files, read without running git."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.store.git_index import MAX_ENTRIES, IndexSnapshot, match_inputs, read_index
from helpers import run_git


def _listed(worktree: Path) -> dict[str, str]:
    """``{path: object ID}`` as git reports the index."""
    listed: dict[str, str] = {}
    for line in run_git(worktree, "ls-files", "-s").stdout.splitlines():
        meta, path = line.split("\t", 1)
        listed[path] = meta.split()[1]
    return listed


def _patch_header(git_index_repo: SimpleNamespace, *, offset: int, value: bytes) -> None:
    index = git_index_repo.git_dir / "index"
    data = bytearray(index.read_bytes())
    data[offset : offset + len(value)] = value
    index.write_bytes(bytes(data))


def test_paths_and_blob_ids_match_git(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is None, snapshot.reason
    assert {path: entry.oid for path, entry in snapshot.entries.items()} == _listed(
        git_index_repo.worktree
    )


def test_snapshot_reports_the_written_version_and_hash_width(
    git_index_repo: SimpleNamespace,
) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.version == git_index_repo.version
    assert snapshot.oid_width == 20
    assert len(snapshot.entries) == len(git_index_repo.paths)
    assert all(len(entry.oid) == 40 for entry in snapshot.entries.values())
    assert snapshot.written_at is not None


def test_v3_extended_flag_marks_only_its_own_path_unknown(
    git_index_repo: SimpleNamespace,
) -> None:
    if git_index_repo.version != 3:
        pytest.skip("covered by the v3 parameter")
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is None, snapshot.reason
    assert snapshot.entries[git_index_repo.skip_worktree].unknown == "skip_worktree"
    marked = sorted(path for path, entry in snapshot.entries.items() if entry.unknown)
    assert marked == [git_index_repo.skip_worktree]


def test_v4_prefix_paths_decode_to_the_index_list(git_index_repo: SimpleNamespace) -> None:
    if git_index_repo.version != 4:
        pytest.skip("covered by the v4 parameter")
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is None, snapshot.reason
    assert tuple(sorted(snapshot.entries)) == tuple(sorted(git_index_repo.paths))
    assert "pkg/deep/nested/module.py" in snapshot.entries


def test_truncated_index_is_unknown_not_an_exception(git_index_repo: SimpleNamespace) -> None:
    index = git_index_repo.git_dir / "index"
    index.write_bytes(index.read_bytes()[:-30])
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is not None
    assert snapshot.reason
    assert snapshot.entries == {}


@pytest.mark.parametrize(
    ("offset", "value", "kind"),
    [
        (0, b"XXXX", "bad_signature"),
        (4, (5).to_bytes(4, "big"), "unsupported_version"),
        (8, (MAX_ENTRIES + 1).to_bytes(4, "big"), "too_many_entries"),
    ],
)
def test_unusable_header_is_unknown_not_an_exception(
    git_index_repo: SimpleNamespace, offset: int, value: bytes, kind: str
) -> None:
    _patch_header(git_index_repo, offset=offset, value=value)
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown == kind
    assert snapshot.entries == {}


def test_rewritten_header_with_the_same_stat_is_not_served_from_a_cache(
    git_index_repo: SimpleNamespace,
) -> None:
    index = git_index_repo.git_dir / "index"
    before = index.stat()
    assert read_index(git_index_repo.git_dir, git_index_repo.worktree).unknown is None
    data = bytearray(index.read_bytes())
    data[4:8] = (7).to_bytes(4, "big")
    index.write_bytes(bytes(data))
    os.utime(index, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = index.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown == "unsupported_version"


def test_missing_index_is_unknown(git_index_repo: SimpleNamespace) -> None:
    (git_index_repo.git_dir / "index").unlink()
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown == "index_missing"
    assert snapshot.entries == {}


def test_unmerged_path_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "conflict"
    (root / "src").mkdir(parents=True)
    (root / "src/a.py").write_text("base\n", encoding="utf-8")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
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
    assert snapshot.unknown is None, snapshot.reason
    entry = snapshot.entries["src/a.py"]
    assert entry.stage != 0
    assert entry.unknown == "unmerged"


def test_sha256_repository_parses_32_byte_object_ids(tmp_path: Path) -> None:
    root = tmp_path / "sha256"
    root.mkdir()
    created = subprocess.run(
        ["git", "init", "--object-format=sha256", str(root)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("this git cannot create SHA-256 repositories")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "sha256")

    snapshot = read_index(root / ".git", root)
    assert snapshot.unknown is None, snapshot.reason
    assert snapshot.oid_width == 32
    assert {path: entry.oid for path, entry in snapshot.entries.items()} == _listed(root)
    assert all(len(entry.oid) == 64 for entry in snapshot.entries.values())


def test_extensions_are_skipped_when_optional_and_refused_when_mandatory(
    git_index_repo: SimpleNamespace,
) -> None:
    index = git_index_repo.git_dir / "index"
    body, trailer = index.read_bytes()[:-20], index.read_bytes()[-20:]

    def with_extension(name: bytes) -> bytes:
        return body + name + (4).to_bytes(4, "big") + b"junk" + trailer

    index.write_bytes(with_extension(b"XYZA"))
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert snapshot.unknown is None, snapshot.reason
    assert len(snapshot.entries) == len(git_index_repo.paths)

    for name in (b"xyzA", b"link", b"sdir"):
        index.write_bytes(with_extension(name))
        refused = read_index(git_index_repo.git_dir, git_index_repo.worktree)
        assert refused.unknown == "unsupported_extension", name
        assert refused.entries == {}


def test_linked_worktree_reads_the_shared_object_format(tmp_path: Path) -> None:
    root = tmp_path / "sha256-worktree"
    root.mkdir()
    created = subprocess.run(
        ["git", "init", "--object-format=sha256", str(root)], capture_output=True, text=True
    )
    if created.returncode != 0:
        pytest.skip("this git cannot create SHA-256 repositories")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
    worktree = tmp_path / "linked"
    run_git(root, "worktree", "add", str(worktree))
    (worktree / "src").mkdir()
    (worktree / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_git(worktree, "add", "-A")

    linked = read_index(worktree / ".git", worktree)
    assert linked.unknown is None, linked.reason
    assert linked.oid_width == 32
    assert {path: entry.oid for path, entry in linked.entries.items()} == _listed(worktree)


def test_unknown_snapshot_starts_empty(git_index_repo: SimpleNamespace) -> None:
    empty = IndexSnapshot({}, None, 20, None, "unsupported_version", "index version 5")
    assert empty.entries == {}
    _patch_header(git_index_repo, offset=0, value=b"XXXX")
    assert read_index(git_index_repo.git_dir, git_index_repo.worktree).entries == {}


def test_match_inputs_globs_the_index_paths(git_index_repo: SimpleNamespace) -> None:
    snapshot = read_index(git_index_repo.git_dir, git_index_repo.worktree)
    assert match_inputs(snapshot, ["src/pkg/a.py"]) == ("src/pkg/a.py",)
    assert match_inputs(snapshot, ["src/**/*.py"]) == (
        "src/main.py",
        "src/pkg/a.py",
        "src/pkg/b.py",
    )
    assert match_inputs(snapshot, ["src/*.py"]) == ("src/main.py",)
    assert match_inputs(snapshot, ["**/*.md"]) == (
        "README.md",
        "docs/guide/intro.md",
        "docs/readme.md",
    )
    assert match_inputs(snapshot, ["nope/**/*.py"]) == ()
    assert match_inputs(snapshot, []) == ()
