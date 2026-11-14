"""Public skills and onboarding docs name only commands and skills that still exist."""

from __future__ import annotations

from pathlib import Path

from command_names import unknown_rail_commands

from context_rail.core.tool_names import REMOVED_COMMANDS

ROOT = Path(__file__).parents[2]


def _public_skill_and_onboarding_docs() -> list[Path]:
    paths = sorted((ROOT / "skills").rglob("*.md"))
    paths += sorted((ROOT / "docs" / "agent-onboarding").rglob("*.md"))
    return [path for path in paths if path.is_file()]


def test_public_skills_name_no_removed_command() -> None:
    docs = _public_skill_and_onboarding_docs()
    assert len(docs) > 10

    for path in docs:
        text = path.read_text(encoding="utf-8")
        for name in REMOVED_COMMANDS:
            assert f"rail {name}" not in text, (path.relative_to(ROOT).as_posix(), name)


def test_public_docs_no_longer_name_test_first() -> None:
    for path in _public_skill_and_onboarding_docs():
        text = path.read_text(encoding="utf-8")
        assert "test-first" not in text, path.relative_to(ROOT).as_posix()


def test_public_docs_commands_resolve() -> None:
    for path in _public_skill_and_onboarding_docs():
        text = path.read_text(encoding="utf-8")
        assert unknown_rail_commands(text) == [], path.relative_to(ROOT).as_posix()
