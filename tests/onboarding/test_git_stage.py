"""The git-driver stage only installs on request in a git checkout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.init import skeleton
from context_rail.gateway.handlers.onboarding_pipeline import git_drivers_stage
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import run_git


def _context(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )


def _args(**overrides: object) -> dict[str, object]:
    return {"no_git_drivers": False, **overrides}


def _store(tmp_path: Path) -> Path:
    skeleton(
        None,
        {"dir": str(tmp_path), "root": "context-rail", "pack": "basic", "key": None},
        "human",
        allow_existing=True,
    )
    return tmp_path / "context-rail"


def test_git_stage_skips_outside_a_checkout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    context = _context(store)
    try:
        result = git_drivers_stage(context, tmp_path, _args())
    finally:
        context.close()

    assert result == {"state": "skipped", "reason": "not_git"}


def test_git_stage_skips_on_a_non_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init")
    store = _store(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    context = _context(store)
    try:
        result = git_drivers_stage(context, tmp_path, _args())
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "no_terminal"
    assert not (store / ".gitattributes").exists()


def test_git_stage_installs_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init")
    store = _store(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    context = _context(store)
    try:
        result = git_drivers_stage(context, tmp_path, _args())
    finally:
        context.close()

    assert result["state"] == "installed"
    assert (store / ".gitattributes").is_file()


def test_git_stage_respects_no_git_drivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init")
    store = _store(tmp_path)
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": pytest.fail("must not prompt")
    )
    context = _context(store)
    try:
        result = git_drivers_stage(context, tmp_path, _args(no_git_drivers=True))
    finally:
        context.close()

    assert result == {"state": "skipped", "reason": "disabled"}
