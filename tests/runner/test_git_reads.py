"""Read-only git helpers that inspect recorded agent work."""

from __future__ import annotations

from pathlib import Path

from context_rail.runner.git import (
    diff_names,
    diff_text,
    first_parent,
    merge_base,
    resolve_commit,
    worktree_paths,
)
from helpers import run_git


def _commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", message)
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init")
    head = _commit(root, {"app.py": "VALUE = 1\n", "src/a/b.py": "B = 1\n"}, "base")
    run_git(root, "branch", "-M", "main")
    return root, head


def test_resolve_commit_full_sha_for_head_branch_and_short_sha(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)

    assert len(head) == 40
    assert resolve_commit(root, "HEAD") == head
    assert resolve_commit(root, "main") == head
    assert resolve_commit(root, head[:8]) == head


def test_resolve_commit_none_for_unknown_and_option_like_rev(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)

    assert resolve_commit(root, "nope") is None
    assert resolve_commit(root, "--all") is None


def test_merge_base_and_first_parent(tmp_path: Path) -> None:
    root, main_head = _repo(tmp_path)
    run_git(root, "checkout", "-b", "work")
    middle = _commit(root, {"a.py": "A = 1\n"}, "one")
    tip = _commit(root, {"b.py": "B = 1\n"}, "two")

    assert merge_base(root, tip, "main") == main_head
    assert first_parent(root, tip) == middle


def test_first_parent_none_for_root_commit(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)

    assert first_parent(root, head) is None


def test_diff_names_lists_changed_paths_with_forward_slashes(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    tip = _commit(root, {"src/a/b.py": "B = 2\n", "docs/x.md": "# x\n"}, "change")

    assert diff_names(root, base, tip) == ["docs/x.md", "src/a/b.py"]


def test_diff_names_lists_a_rename_as_its_new_path(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    run_git(root, "mv", "app.py", "core.py")
    run_git(root, "commit", "-m", "rename")
    tip = run_git(root, "rev-parse", "HEAD").stdout.strip()

    assert diff_names(root, base, tip) == ["core.py"]


def test_diff_text_truncates_at_max_chars(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    big = "".join(f"LINE_{index} = {index}\n" for index in range(2000))
    tip = _commit(root, {"big.py": big}, "big")

    text, truncated = diff_text(root, base, tip, max_chars=100)
    assert truncated is True
    assert len(text) <= 100
    assert text.startswith("diff --git")

    whole, truncated = diff_text(root, base, tip, max_chars=10_000_000)
    assert truncated is False
    assert "LINE_1999 = 1999" in whole


def test_worktree_paths_lists_main_and_added_worktree(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    other = tmp_path / "wt"
    run_git(root, "worktree", "add", str(other), "-b", "work")

    paths = worktree_paths(root)

    assert root.resolve() in paths
    assert other.resolve() in paths
