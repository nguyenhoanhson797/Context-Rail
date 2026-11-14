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
    review = [
        line
        for line in section.splitlines()
        if line.startswith("- Review checklist — ")
    ]
    assert len(review) == 1
    assert review[0].endswith("(2 copies)")


def test_overview_ends_with_the_cto_role_and_its_skills(
    approved_store: Path,
) -> None:
    result = invoke(
        "overview", {"max_chars": 20000}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    text = result["value"]["text"]
    assert "## Your role" in text
    assert "You are acting as: cto" in text
    assert "- rail-coordinate:" in text
    assert text.index("## Conventions and memory") < text.index("## Your role")


def test_the_role_block_survives_a_tight_budget(approved_store: Path) -> None:
    result = invoke(
        "overview", {"max_chars": 1500}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    text = result["value"]["text"]
    assert len(text) <= 1500
    assert "## Your role" in text
    assert "You are acting as: cto" in text

    tight = invoke(
        "overview", {"max_chars": 700}, store=approved_store, actor="cto"
    )
    assert is_ok(tight), tight
    tight_text = tight["value"]["text"]
    assert len(tight_text) <= 700
    assert "You are acting as:" in tight_text


def test_conventions_skip_skills_listed_in_the_role(approved_store: Path) -> None:
    result = invoke(
        "overview", {"max_chars": 20000}, store=approved_store, actor="cto"
    )

    assert is_ok(result), result
    section = _section(result["value"]["text"], "## Conventions and memory")
    for line in section.splitlines():
        assert not line.startswith("- Rail coordinate — ")

