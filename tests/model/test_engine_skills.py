"""The engine ships four skills, and every packaged pin must resolve to one."""

from __future__ import annotations

import pytest
from command_names import rail_commands, unknown_rail_commands

from context_rail.model import cto
from context_rail.model.cto import load_engine_skills, load_skill_sources

#: Words a shipped skill may not carry: the removed launcher, its tools and models.
FORBIDDEN = (
    "worktree",
    "droid",
    "rail-report",
    "parent writes",
    "loop dispatch",
    "loop merge",
    "loop tick",
    "cto tick",
    "run record",
    "runner",
    "claude",
    "codex",
    "opencode",
    "cursor",
    "gemini",
)


def test_engine_skills_load_and_validate() -> None:
    assert sorted(load_engine_skills()) == [
        "lean-testing",
        "rail-coordinate",
        "rail-recover",
        "rail-triage",
    ]


def test_a_pack_skill_overrides_an_engine_skill_of_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "---\nname: lean-testing\n---\n### Pack version\n"
    monkeypatch.setattr(cto, "load_pack_skills", lambda pack: {"lean-testing": text})

    assert load_skill_sources("basic")["lean-testing"] == text


def test_engine_skill_commands_resolve() -> None:
    for name, text in load_engine_skills().items():
        assert rail_commands(text), f"{name} names no rail command"
        assert unknown_rail_commands(text) == [], name


def test_engine_skills_name_no_launcher_or_tool() -> None:
    for name, text in load_engine_skills().items():
        lowered = text.lower()
        for word in FORBIDDEN:
            assert word not in lowered, (name, word)
