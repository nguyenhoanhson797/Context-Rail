"""The skeleton stage writes absent parts and resumes an existing store."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.gateway.handlers.init import scaffold, skeleton
from context_rail.store.write import WriteRefused


def _args(tmp_path: Path, **overrides: object) -> dict[str, object]:
    return {
        "dir": str(tmp_path),
        "root": "context-rail",
        "pack": "basic",
        "key": None,
        **overrides,
    }


def test_skeleton_writes_the_manifest_and_pinned_folders(tmp_path: Path) -> None:
    result = skeleton(None, _args(tmp_path), "human", allow_existing=True)

    store = tmp_path / "context-rail"
    assert result["state"] == "written"
    assert result["written"] == ["rail.yaml", ".gitignore"]
    assert (store / "rail.yaml").is_file()
    assert (store / ".gitignore").read_bytes() == b".rail-local/\n"
    assert (store / "items").is_dir()
    assert (store / "workspace" / "skills").is_dir()


def test_skeleton_resumes_without_rewriting(tmp_path: Path) -> None:
    first = skeleton(None, _args(tmp_path), "human", allow_existing=True)
    before = (tmp_path / "context-rail" / "rail.yaml").read_bytes()

    second = skeleton(None, _args(tmp_path), "human", allow_existing=True)

    assert second["state"] == "unchanged"
    assert second["written"] == []
    assert (tmp_path / "context-rail" / "rail.yaml").read_bytes() == before
    assert first["pack"] == second["pack"] == "basic"


def test_skeleton_refuses_a_different_pack(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    before = (tmp_path / "context-rail" / "rail.yaml").read_bytes()

    with pytest.raises(WriteRefused) as refused:
        skeleton(
            None,
            _args(tmp_path, pack="flow"),
            "human",
            allow_existing=True,
        )

    assert refused.value.code == "CONFLICT"
    assert refused.value.details == {"stored": "basic", "requested": "flow"}
    assert (tmp_path / "context-rail" / "rail.yaml").read_bytes() == before


def test_skeleton_refuses_a_malformed_existing_manifest(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    (store / "rail.yaml").write_bytes(b"schema: [")

    with pytest.raises(WriteRefused) as refused:
        skeleton(None, _args(tmp_path), "human", allow_existing=True)

    assert refused.value.code == "CONFLICT"
    assert (store / "rail.yaml").read_bytes() == b"schema: ["


def test_scaffold_keeps_the_old_refusal(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)

    with pytest.raises(WriteRefused) as refused:
        scaffold(None, _args(tmp_path), "human")

    assert refused.value.code == "CONFLICT"
    assert refused.value.message == "a store already exists here"


def test_skeleton_adds_a_missing_gitignore_on_resume(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    (tmp_path / "context-rail" / ".gitignore").unlink()

    result = skeleton(None, _args(tmp_path), "human", allow_existing=True)

    assert result["state"] == "written"
    assert result["written"] == [".gitignore"]
    assert (tmp_path / "context-rail" / ".gitignore").is_file()
