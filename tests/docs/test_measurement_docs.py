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
