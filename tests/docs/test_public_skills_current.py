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


def test_command_check_flags_removed_and_unknown_commands() -> None:
    bad = (
        "`rail <seat> bogus`",
        "`rail --actor cto loop dispatch`",
        "`rail loop dispatch x`",
        "`rail cto tick`",
        "`rail chnage move x done`",
        "`rail nosuch ...`",
    )
    for example in bad:
        assert unknown_rail_commands(example) == [example.strip("`")], example


def test_the_loop_skill_states_one_loop_and_the_recording_rule() -> None:
    text = (ROOT / "skills" / "context-rail-loop" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for line in (
        "plan rail change add task",
        "pick rail next",
        "hand rail loop brief <taskId>",
        "work ... any harness, any sub-agents ...",
        "report rail loop result <taskId> --body-file <file>",
        "review rail loop review <taskId> --result <id> --verdict approve",
        "close rail change move <taskId> <done-status>",
        "learn rail loop report <taskId> --report-file <json>",
    ):
        assert line in flat, line
    for rule in (
        "Navigation and orientation are not recorded.",
        "a commitless result naming the model and harness that produced it",
        "must cite evidence that already exists in the store",
        "the parent creates the task, passes the brief, and records the answer "
        "as the accountable producer",
        "Use the `task` preset for research, not `question`",
        "`loop result` is the report step; `loop report` is optional findings for triage.",
    ):
        assert rule in flat, rule
