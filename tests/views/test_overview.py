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


def test_overview_prints_each_identical_skill_once(
    approved_store: Path, tmp_path: Path
) -> None:
    shipped = (
        approved_store / "workspace" / "profiles" / "reviewer" / "skills" / "review-checklist.md"
    ).read_text(encoding="utf-8")
    body = tmp_path / "review-checklist.md"
    body.write_text(shipped.split("\n---\n", 1)[1], encoding="utf-8", newline="\n")
    copied = invoke(
        "add",
        {
            "preset": "skill",
            "title": "Review checklist",
            "set": ["version=1", "description=The checks a reviewer runs."],
            "body_file": str(body),
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(copied), copied

    result = invoke(
        "overview", {"max_chars": 20000}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    section = _section(result["value"]["text"], "## Conventions and memory")
    assert section.count("- Review checklist — ") == 1
    assert section.count("(2 copies)") == 1
    start = section.index("- Review checklist — ")
    marker = section.index("(2 copies)")
    assert start < marker
    for title in (
        "Lean testing",
        "Coordinate the rail",
        "Recover from a refusal",
        "Triage reports",
    ):
        assert f"- {title} — " not in section[start:marker], title
