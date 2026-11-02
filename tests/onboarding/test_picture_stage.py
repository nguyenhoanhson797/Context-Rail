"""The picture stage skips an existing picture and prompts on a fresh store."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.init import skeleton
from context_rail.gateway.handlers.onboarding_pipeline import (
    picture_stage,
    workspace_seed,
)
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack


def _args(tmp_path: Path) -> dict[str, Any]:
    return {
        "dir": str(tmp_path),
        "root": "context-rail",
        "pack": "basic",
        "key": None,
        "interview": False,
        "answers_file": None,
        "draft_file": None,
        "confirm": None,
    }


def _context(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )


def test_picture_stage_skips_an_existing_picture(unapproved_store: Path) -> None:
    context = _context(unapproved_store)
    try:
        result = picture_stage(
            context,
            unapproved_store.parent,
            actor="human",
            args=_args(unapproved_store.parent),
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "picture_present"
    assert result["slugs"][0] == "ship-invoicing"


def test_picture_stage_defers_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        result = picture_stage(
            context, tmp_path, actor="human", args=_args(tmp_path)
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "no_picture_source"
    assert result["command"] == "rail init --interview"
    assert not (store / "charter.md").exists()


def test_picture_stage_defers_when_the_human_does_not_enter_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        result = picture_stage(
            context, tmp_path, actor="human", args=_args(tmp_path)
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "deferred"
    assert not (store / "charter.md").exists()


def test_picture_stage_defers_on_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

        def eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        result = picture_stage(
            context, tmp_path, actor="human", args=_args(tmp_path)
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "deferred"


def test_picture_stage_interviews_a_fresh_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    picture_answers: dict[str, Any],
) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        monkeypatch.setattr(
            "context_rail.gateway.handlers.onboarding._interview",
            lambda pack, root: picture_answers,
        )
        result = picture_stage(
            context, tmp_path, actor="human", args=_args(tmp_path)
        )
    finally:
        context.close()

    assert result["state"] == "written"
    assert "ship-invoicing" in result["picture"]
    assert (store / "project" / "ship-invoicing.md").is_file()
    assert (store / "charter.md").is_file()
