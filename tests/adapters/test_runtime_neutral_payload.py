"""The generated agent instructions describe the record loop, not a launcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke

LAUNCHER_WORDS = ("loop dispatch", "loop tick", "loop merge", "cto tick", "droid", "rail-report")


def _sync(store: Path, *harness: str) -> dict:
    result = invoke("adapters sync", {"harness": list(harness)}, store=store, actor="cto")
    assert result["ok"] is True, result
    return result


def _agents(store: Path) -> str:
    _sync(store, "agents")
    return (store.parent / "AGENTS.md").read_text(encoding="utf-8")


def test_agents_payload_describes_native_subagent_loop(approved_store: Path) -> None:
    text = _agents(approved_store)

    assert "rail loop brief" in text
    assert "rail loop candidate" in text
    assert "rail loop review" in text
    assert "sub-agent" in text
    assert "rail loop report" in text
    assert "rail check run" in text


def test_agents_payload_names_no_launcher(approved_store: Path) -> None:
    lowered = _agents(approved_store).lower()

    for word in LAUNCHER_WORDS:
        assert word not in lowered, word
    assert "does not run agents" in lowered or "does not start agents" in lowered


def test_human_only_actions_include_the_upgrade_and_its_delegation(
    approved_store: Path,
) -> None:
    text = _agents(approved_store)

    assert "ops upgrade" in text
    assert "ops delegate" in text
    assert "delegates that exact action" in text
    assert "role-mode store" in text


@pytest.mark.parametrize("role", ["cto", "implementer", "reviewer"])
def test_claude_agent_files_inherit_model(approved_store: Path, role: str) -> None:
    _sync(approved_store, "claude")

    agent = (approved_store.parent / f".claude/agents/{role}.md").read_text(encoding="utf-8")

    assert "model: inherit" in agent


def test_a_stale_model_field_in_a_profile_does_not_reach_the_agent_file(
    approved_store: Path,
) -> None:
    profile = approved_store / "workspace/profiles/implementer/profile.md"
    text = profile.read_text(encoding="utf-8").replace("\r\n", "\n")
    profile.write_text(text.replace("\nschema:", "\nmodel: claude-opus\nschema:", 1), encoding="utf-8", newline="\n")

    _sync(approved_store, "claude")

    agent = (approved_store.parent / ".claude/agents/implementer.md").read_text(encoding="utf-8")
    assert "model: inherit" in agent and "claude-opus" not in agent