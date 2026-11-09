"""The bounded overview: unit work, not containers or duplicated memory."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_overview_counts_only_the_operating_unit_in_the_workflow_section(
    approved_store: Path,
) -> None:
    added = invoke(
        "add",
        {
            "preset": "task",
            "title": "A task",
            "rel": ["part_of=send-invoice"],
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]

    result = invoke(
        "overview", {"max_chars": 20000}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    section = _section(result["value"]["text"], "## Workflows and their next actions")
    assert slug in section
    assert "ship-invoicing" not in section
    assert "send-invoice" not in section


def test_overview_prints_each_identical_skill_once(approved_store: Path) -> None:
    result = invoke(
        "overview", {"max_chars": 20000}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    section = _section(result["value"]["text"], "## Conventions and memory")
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert sum(line.startswith("- Review checklist") for line in lines) == 1
    assert sum(line.startswith("- Test first") for line in lines) == 1
