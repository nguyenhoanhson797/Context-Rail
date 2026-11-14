"""The work brief carries the role, its rules and its skills."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from helpers import as_human
from test_work_brief import brief, candidate

from context_rail.context.work_brief import WORK_BRIEF_SECTIONS
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke

IMPLEMENTER = "implementer"
REVIEWER_PROFILE = "workspace/profiles/reviewer/profile.md"
LEAN_TESTING_NOTE = "workspace/profiles/implementer/skills/lean-testing.md"
PROFILE_LINE = (
    "You implement exactly one Item in your own checkout and leave one commit."
)


def _etag(store: Path, slug: str) -> str:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def _replace_body(path: Path, body: str) -> None:
    """Swap a note's body and keep its frontmatter, as an author's edit would."""
    text = path.read_text(encoding="utf-8")
    front, marker, _ = text.partition("\n---\n")
    assert marker, text
    path.write_text(front + marker + body, encoding="utf-8", newline="\n")


def _break_budget(path: Path) -> None:
    """Make a profile fail to parse by giving its budget a list, not a mapping."""
    text = path.read_text(encoding="utf-8")
    broken = re.sub(r"(?m)^budget:.*$", "budget: [not, a, mapping]", text)
    assert broken != text, text
    path.write_text(broken, encoding="utf-8", newline="\n")


def test_implementer_brief_names_the_role_rules_and_skill(
    leaf_item: SimpleNamespace,
) -> None:
    result = brief(leaf_item)

    assert is_ok(result), result
    value = result["value"]
    assert f"You are acting as: {IMPLEMENTER}" in value["text"]
    assert PROFILE_LINE in value["text"]
    assert "### lean-testing" in value["text"]
    assert value["acting"]["skills"][0]["name"] == "lean-testing"


def test_brief_uses_the_target_profile_budget(leaf_item: SimpleNamespace) -> None:
    lowered = invoke(
        "update",
        {
            "slug": IMPLEMENTER,
            "set": ["budget={max_chars: 1500, max_docs: 20}"],
            "expect": _etag(leaf_item.store, IMPLEMENTER),
        },
        store=leaf_item.store,
        actor="cto",
    )
    assert is_ok(lowered), lowered

    result = brief(leaf_item, actor="cto")

    assert is_ok(result), result
    text = result["value"]["text"]
    assert f"You are acting as: {IMPLEMENTER}" in text
    assert len(text) <= 1500
    explicit = brief(leaf_item, actor="cto", max_chars=100)
    assert is_ok(explicit), explicit
    assert len(explicit["value"]["text"]) <= 100
    cto = invoke("show", {"slug": "cto"}, store=leaf_item.store, actor="human")
    assert cto["value"]["doc"]["fields"]["budget"] == {
        "max_chars": 12000,
        "max_docs": 20,
    }


def test_a_missing_pin_is_named_not_raised(leaf_item: SimpleNamespace) -> None:
    (leaf_item.store / LEAN_TESTING_NOTE).unlink()

    result = brief(leaf_item)

    assert is_ok(result), result
    value = result["value"]
    assert "missing (run rail ops upgrade)" in value["text"]
    assert value["acting"]["skills"][0]["missing"] is True


def test_role_mode_brief_without_a_seat_names_the_role(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        result = invoke(
            "brief",
            {"item": role_store.o1, "role": "sprint-lead"},
            store=role_store.store,
            actor=None,
        )

    assert is_ok(result), result
    value = result["value"]
    assert value["sections"]["role"].startswith("You are acting as: sprint-lead")
    assert value["acting"]["profile"] is None


def test_a_tiny_budget_collapses_skills_first(leaf_item: SimpleNamespace) -> None:
    result = brief(leaf_item, max_chars=600)

    assert is_ok(result), result
    text = result["value"]["text"]
    assert len(text) <= 600
    assert "(read: rail read show" in text
    assert "## objective" in text
    assert "Choose the smallest check that proves the change" not in text


def test_body_headings_never_collide_with_sections(leaf_item: SimpleNamespace) -> None:
    _replace_body(
        leaf_item.store / LEAN_TESTING_NOTE,
        "### Lean testing\n\nChoose the smallest check that proves the change.\n\n"
        "## Heading\n\nBody text under a heading.\n",
    )

    result = brief(leaf_item)

    assert is_ok(result), result
    text = result["value"]["text"]
    headings = tuple(line[3:] for line in text.splitlines() if line.startswith("## "))
    assert headings == WORK_BRIEF_SECTIONS
    assert "#### Heading" in text


def test_the_brief_no_longer_prescribes_test_first(leaf_item: SimpleNamespace) -> None:
    result = brief(leaf_item)

    assert is_ok(result), result
    assert "failing test" not in result["value"]["text"]


def test_a_malformed_reviewer_profile_degrades_to_the_role_line(
    leaf_item: SimpleNamespace,
) -> None:
    candidate(leaf_item)
    _break_budget(leaf_item.store / REVIEWER_PROFILE)

    result = brief(leaf_item, actor="cto", role="reviewer")

    assert is_ok(result), result
    role = result["value"]["sections"]["role"]
    assert role.startswith(f"You are acting as: reviewer (profile {REVIEWER_PROFILE}")
    assert "does not parse" in role
    assert result["value"]["acting"]["profile"] == REVIEWER_PROFILE
