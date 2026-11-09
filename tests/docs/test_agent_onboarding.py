"""The portable agent-onboarding kit stays complete and harness-agnostic."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_the_kit_index_names_every_entry_point() -> None:
    text = _read("docs/agent-onboarding/README.md")

    for phrase in (
        "AGENTS.md",
        "SKILL.md",
        "any harness",
        "rail init",
        "rail types",
        "MCP",
        "references/",
    ):
        assert phrase in text, phrase


def test_the_portable_agents_file_teaches_the_safe_protocol() -> None:
    text = _read("docs/agent-onboarding/AGENTS.template.md")

    for phrase in (
        "Never parse",
        "rail types",
        "rail next",
        "rail read show",
        "rail why",
        "--expect",
        "--key",
        "--rel",
        "--set",
        "AMBIGUOUS_FOCUS",
        "human-only",
        "immutable",
    ):
        assert phrase in text, phrase


def test_the_onboarding_skill_is_discoverable_and_complete() -> None:
    text = _read("skills/context-rail-v3-onboarding/SKILL.md")

    assert text.startswith("---\n")
    assert "name: context-rail-v3-onboarding" in text
    assert "description:" in text
    for phrase in (
        "rail init",
        "approve charter",
        "rail overview",
        "rail ops doctor",
        "context-rail-v3",
    ):
        assert phrase in text, phrase


def test_the_capability_reference_covers_the_full_power_surface() -> None:
    text = _read("docs/agent-onboarding/references/capabilities.md")

    for phrase in (
        "primitive",
        "relation",
        "category",
        "operating",
        "workflow",
        "placement",
        "check",
        "dispatch",
        "review",
        "merge",
        "MCP",
        "semantic_digest",
        "Records",
    ):
        assert phrase in text, phrase


def test_the_harness_reference_covers_the_common_harnesses() -> None:
    text = _read("docs/agent-onboarding/references/harnesses.md")

    for phrase in ("Claude Code", "Cursor", "Gemini", "OpenCode", "Codex", "MCP"):
        assert phrase in text, phrase


def test_every_playbook_recovery_and_prompt_is_present() -> None:
    checks = {
        "docs/agent-onboarding/references/playbooks.md": (
            "Greenfield code project",
            "Migrating a legacy",
        ),
        "docs/agent-onboarding/references/refusals.md": (
            "Recovery order",
            "AMBIGUOUS_FOCUS",
        ),
        "docs/agent-onboarding/references/prompts.md": (
            "Universal onboarding agent",
            "CTO daily driver",
            "Implementer worker",
        ),
    }
    for rel, phrases in checks.items():
        text = _read(rel)
        for phrase in phrases:
            assert phrase in text, f"{rel}: {phrase}"
