"""Old-side changed line ranges between two commits."""

from __future__ import annotations

from pathlib import Path

from helpers import run_git

from context_rail.runner.git import changed_line_ranges


def _lines(count: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {number}\n" for number in range(1, count + 1))


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8", newline="\n")


def _commit(root: Path, message: str) -> str:
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", message)
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def _ranges(root: Path, base: str, candidate: str) -> dict:
    return changed_line_ranges(
        worktree=root, confine_to=root, base_commit=base, candidate_commit=candidate
    )


def test_each_kind_of_change_maps_to_old_side_ranges(tmp_path: Path) -> None:
    run_git(tmp_path, "init")
    for name in ("modified.txt", "deleted.txt", "inserted.txt"):
        _write(tmp_path, name, _lines(10))
    _write(tmp_path, "before.txt", _lines(5, "moved"))
    _write(tmp_path, "query.sql", "select 1;\n-- old note\nselect 2;\n")
    base = _commit(tmp_path, "base")

    _write(tmp_path, "modified.txt", _lines(10).replace("line 3\n", "LINE 3\n").replace("line 8\n", "LINE 8\n"))
    _write(tmp_path, "deleted.txt", "".join(f"line {n}\n" for n in (1, 2, 3, 4, 5, 8, 9, 10)))
    _write(tmp_path, "inserted.txt", _lines(9) + "extra\n" + "line 10\n")
    _write(tmp_path, "new.txt", _lines(3))
    (tmp_path / "before.txt").rename(tmp_path / "after.txt")
    _write(tmp_path, "query.sql", "select 1;\nselect 2;\n")
    candidate = _commit(tmp_path, "change")

    result = _ranges(tmp_path, base, candidate)

    assert result["complete"] is True
    assert result["files"] == {
        "after.txt": [],
        "before.txt": [(1, 5)],
        "deleted.txt": [(6, 7)],
        "inserted.txt": [(9, 9)],
        "modified.txt": [(3, 3), (8, 8)],
        "new.txt": [],
        "query.sql": [(2, 2)],
    }


def test_a_missing_revision_is_a_refusal_not_an_empty_diff(tmp_path: Path) -> None:
    import pytest

    from context_rail.store.write import WriteRefused

    run_git(tmp_path, "init")
    _write(tmp_path, "a.txt", "a\n")
    head = _commit(tmp_path, "base")

    with pytest.raises(WriteRefused):
        _ranges(tmp_path, head, "0" * 40)
