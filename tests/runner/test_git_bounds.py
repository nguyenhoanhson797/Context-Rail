"""A truncated git listing is never proof: the proof layer refuses it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

import context_rail.runner.git as git_module
import context_rail.runner.git_sync as git_sync_module
from context_rail.runner.git import (
    diff_names,
    ls_files,
    ls_files_staged,
    status_porcelain,
)
from context_rail.store.write import WriteRefused
from helpers import run_git


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "context-rail").mkdir()
    (root / "context-rail" / "rail.yaml").write_text(
        "schema: 3\n", encoding="utf-8", newline="\n"
    )
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
    return root


def _truncated(command: list[str], **_: Any) -> dict[str, Any]:
    return {
        "exit": 0,
        "timed_out": False,
        "duration_s": 0.0,
        "stdout": "app.py\0",
        "stderr": "",
        "omitted_bytes": 4096,
        "digest": "sha256:0000",
    }


def _prove_refused(call: Callable[[], Any]) -> None:
    with pytest.raises(WriteRefused) as caught:
        call()
    assert caught.value.code == "INTEGRATION_FAILURE"
    assert caught.value.details.get("process") == "git"


@pytest.mark.parametrize(
    "reader",
    [
        lambda root: ls_files(root, root),
        lambda root: ls_files_staged(root, root),
        lambda root: status_porcelain(root, root),
        lambda root: diff_names(root, "HEAD", "HEAD"),
    ],
)
def test_every_proof_listing_refuses_truncated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: Callable[[Path], Any]
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(git_module, "run", _truncated)

    _prove_refused(lambda: reader(root))


def test_checked_refuses_truncation_centrally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(git_module, "run", _truncated)

    _prove_refused(
        lambda: git_module._checked(["status"], cwd=root, confine_to=root)
    )


def test_a_clean_complete_listing_keeps_its_mapping(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    committed = ls_files(root, root)
    staged = ls_files_staged(root, root)

    assert list(committed) == ["app.py", "context-rail/rail.yaml"]
    assert staged == committed
    assert status_porcelain(root, root) == ()


def test_dirty_outside_refuses_truncated_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_sync_module, "run", _truncated)

    _prove_refused(
        lambda: git_sync_module.dirty_outside(tmp_path, tmp_path, "context-rail")
    )
