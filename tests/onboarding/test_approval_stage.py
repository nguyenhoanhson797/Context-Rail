"""The approval stage offers the fresh phrase and never guesses a human."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.handlers.onboarding_pipeline import approval_stage
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack


def _context(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )


def _args(**overrides: object) -> dict[str, object]:
    return {"no_approve": False, **overrides}


def test_approval_stage_records_the_fresh_phrase(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = ["Ada", "APPROVE charter"]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": replies.pop(0))
    context = _context(unapproved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=True, args=_args()
        )
    finally:
        context.close()

    assert result["state"] == "approved"
    shown = invoke("show", {"slug": "charter"}, store=unapproved_store, actor="human")
    assert shown["value"]["approval"]["state"] == "approved"


def test_approval_stage_is_unchanged_when_already_approved(
    approved_store: Path,
) -> None:
    context = _context(approved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=True, args=_args()
        )
    finally:
        context.close()

    assert result == {"state": "unchanged", "approval": "approved"}


def test_no_approve_skips_without_prompting(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": pytest.fail("must not prompt")
    )
    context = _context(unapproved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=True, args=_args(no_approve=True)
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "no_approve"
    assert "rail --actor human approve charter" in result["command"]


def test_an_unnamed_human_is_not_enough(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": pytest.fail("must not prompt")
    )
    context = _context(unapproved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=False, args=_args()
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "actor_not_named"


def test_a_non_terminal_skips_without_prompting(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": pytest.fail("must not prompt")
    )
    context = _context(unapproved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=True, args=_args()
        )
    finally:
        context.close()

    assert result["state"] == "skipped"
    assert result["reason"] == "no_terminal"


def test_a_wrong_phrase_refuses_without_writing(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = ["Ada", "APPROVE chart"]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": replies.pop(0))
    context = _context(unapproved_store)
    try:
        result = approval_stage(
            context, actor="human", actor_named=True, args=_args()
        )
    finally:
        context.close()

    assert result["state"] == "refused"
    assert result["reason"] == "VALIDATION_FAILED"
    shown = invoke("show", {"slug": "charter"}, store=unapproved_store, actor="human")
    assert shown["value"]["approval"]["state"] == "missing"
