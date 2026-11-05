"""The agent-file stage reuses the adapter renderer and its refusals."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.onboarding_pipeline import agents_stage
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.write import WriteRefused


def _context(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )


def _args(**overrides: object) -> dict[str, object]:
    return {"harness": [], **overrides}


def test_agents_stage_writes_and_then_skips(unapproved_store: Path) -> None:
    context = _context(unapproved_store)
    try:
        first = agents_stage(context, _args())
        second = agents_stage(context, _args())
    finally:
        context.close()

    assert first["state"] == "written"
    assert "AGENTS.md" in first["written"]
    agents = unapproved_store.parent / "AGENTS.md"
    assert agents.is_file()
    text = agents.read_text(encoding="utf-8")
    assert "Read before you act" in text and "rail types" in text
    assert (unapproved_store.parent / ".agents" / "skills").is_dir()
    assert second["state"] == "unchanged"


def test_agents_stage_passes_the_harness_selection(unapproved_store: Path) -> None:
    context = _context(unapproved_store)
    try:
        result = agents_stage(context, _args(harness=["claude"]))
    finally:
        context.close()

    assert result["state"] == "written"
    assert (unapproved_store.parent / "CLAUDE.md").is_file()
    assert not (unapproved_store.parent / "AGENTS.md").exists()


def test_agents_stage_returns_the_adapter_refusal(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise WriteRefused("CONFLICT", "generated adapter files changed outside rail")

    monkeypatch.setattr("context_rail.gateway.handlers.adapters.sync", refuse)
    context = _context(unapproved_store)
    try:
        result = agents_stage(context, _args())
    finally:
        context.close()

    assert result["state"] == "refused"
    assert result["reason"] == "CONFLICT"
    assert "generated adapter files" in result["message"]
