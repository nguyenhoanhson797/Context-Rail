"""The public docs describe effort tracking, and what it cannot do."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_COMMAND_FILES = (
    "docs/how-it-works.md",
    "docs/agent-onboarding/references/commands.md",
    "skills/context-rail-v3/references/commands.md",
)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_every_command_reference_names_the_usage_rollup() -> None:
    for rel in _COMMAND_FILES:
        text = _read(rel)
        assert "ops measure usage" in text, rel
        assert "--group-by" in text, rel


def test_how_it_works_says_effort_is_reported_not_measured() -> None:
    text = _read("docs/how-it-works.md")

    for phrase in (
        "rail loop candidate",
        "--duration",
        "--usage",
        "never measures it",
        "no live timer",
        "no token meter",
        "no price table",
        "never counted as zero",
        "never applies a change",
    ):
        assert phrase in text, phrase


def test_how_it_works_describes_the_effort_block_and_the_join() -> None:
    flat = " ".join(_read("docs/how-it-works.md").split())
    for phrase in (
        "leading `---` block",
        "duration_s: 5400",
        "usage: {tokens: 152000, requests: 42}",
        "never money",
        "join on the commit, once per attempt",
        "a result with no commit is its own pending attempt",
        "`attempts` still counts candidates",
        "seconds and usage counters sum the joined effort",
        "navigation is not recorded",
        "a commitless result naming the model and harness that produced it",
    ):
        assert phrase in flat, phrase
    assert "effort block" in _read("docs/building-blocks.md")


def test_the_public_skills_name_the_usage_rollup() -> None:
    for rel in (
        "skills/context-rail-v3/SKILL.md",
        "skills/context-rail-v3-loop/SKILL.md",
        "skills/context-rail-v3-ops/SKILL.md",
    ):
        assert "measure usage" in _read(rel), rel


def test_the_command_references_note_the_effort_block_on_result() -> None:
    for rel in (
        "docs/agent-onboarding/references/commands.md",
        "skills/context-rail-v3/references/commands.md",
    ):
        flat = " ".join(_read(rel).split())
        assert "effort block" in flat, rel
        assert "duration_s" in flat, rel
        assert "money is not a counter" in flat, rel
