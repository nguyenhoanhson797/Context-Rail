"""Completeness checks for the committed Stage 1b plan document."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PLAN = _REPO / "docs/superpowers/plans/2026-10-28-v3-stage-1b-cto-slice.md"

PINNED_LITERALS = (
    "under 150 ms",
    "under 300 ms",
    "under 1 s",
    "under 10 s",
    "under 60 s",
    "under 200 ms",
    "INDEX_SCHEMA=2",
    "APPROVE <target>",
    "cto brief",
    "cto tick",
    "adapters sync",
    "custom:Deepseek-V4-flash-0",
    "custom:gemini-3.8-flash-LTN-0",
    "read_index",
    "semantic_hash",
    "protected_snapshot",
    "approval_state",
    "snapshot_digest",
)

_TASK_HEADING = re.compile(r"^### Task (1b-T\d+):", re.M)
_COMMIT = re.compile(r'git commit -m "(?:feat|test|docs)\(')


def _plan_text() -> str:
    return _PLAN.read_text(encoding="utf-8")


def _task_sections() -> list[tuple[str, str]]:
    text = _plan_text()
    matches = list(_TASK_HEADING.finditer(text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[match.start() : end]))
    return sections


def test_plan_has_seventeen_task_sections_in_order() -> None:
    codes = [code for code, _ in _task_sections()]
    assert codes == [f"1b-T{number}" for number in range(1, 18)]


def test_every_task_section_names_a_test_path_and_a_commit_subject() -> None:
    missing: list[str] = []
    for code, section in _task_sections():
        if "tests/" not in section:
            missing.append(f"{code}: no tests/ path")
        if not _COMMIT.search(section):
            missing.append(f"{code}: no conventional commit subject")
    assert missing == []


def test_pinned_contract_literals_are_present() -> None:
    text = _plan_text()
    absent = [literal for literal in PINNED_LITERALS if literal not in text]
    assert absent == []


def test_plan_names_only_engine_error_codes() -> None:
    from context_rail.core.result import ERROR_CODES

    text = _plan_text()
    named = set(re.findall(r"\b[A-Z][A-Z_]{3,}\b", text))
    code_like = {
        name
        for name in named
        if name.endswith(
            ("_FAILED", "_UNSUPPORTED", "_DENIED", "_VIOLATED", "_ERROR", "_NOT_FOUND")
        )
    }
    assert code_like <= set(ERROR_CODES), sorted(code_like - set(ERROR_CODES))
