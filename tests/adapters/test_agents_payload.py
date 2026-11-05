"""The generated AGENTS.md is an onboarding page, not just a file list."""

from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import invoke


def test_the_generated_agents_block_teaches_the_rail_protocol(
    skill_store: Path,
) -> None:
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is True, result

    text = (skill_store.parent / "AGENTS.md").read_text(encoding="utf-8")
    for phrase in (
        "Read before you act",
        "Never parse",
        "rail types",
        "rail next",
        "rail show",
        "rail why",
        "rail model",
        "--expect",
        "--key",
        "--rel",
        "--set",
        "rail change move",
        "human-only",
        "AMBIGUOUS_FOCUS",
        "rail ops reindex",
        "rail serve mcp",
    ):
        assert phrase in text, phrase
