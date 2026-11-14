"""The work brief carries the role, its rules and its skills."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.context.work_brief import WORK_BRIEF_SECTIONS
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import as_human

IMPLEMENTER = "implementer"
LEAN_TESTING_NOTE = "workspace/profiles/implementer/skills/lean-testing.md"
PROFILE_LINE = "You implement exactly one Item in your own checkout and leave one commit."


def _feature(store: Path) -> str:
    added = invoke("add", {"preset": "task", "title": "Feature A"}, store=store, actor="cto")
    assert is_ok(added), added
    return added["value"]["slug"]


def _brief(store: Path, item: str, actor: str = "dev", **args: Any) -> dict[str, Any]:
    return invoke("brief", {"item": item, **args}, store=store, actor=actor)


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _replace_body(path: Path, body: str) -> None:
    """Swap a note's body and keep its frontmatter, as an author's edit would."""
    text = path.read_text(encoding="utf-8")
    front, marker, _ = text.partition("\n---\n")
    assert marker, text
    path.write_text(front + marker + body, encoding="utf-8", newline="\n")


def test_implementer_brief_names_the_role_rules_and_skill(approved_store: Path) -> None:
    item = _feature(approved_store)

    result = _brief(approved_store, item)

    assert is_ok(result), result
    value = result["value"]
    assert f"You are acting as: {IMPLEMENTER}" in value["text"]
    assert PROFILE_LINE in value["text"]
    assert "### lean-testing" in value["text"]
    assert value["acting"]["skills"][0]["name"] == "lean-testing"


def test_brief_uses_the_target_profile_budget(approved_store: Path) -> None:
    item = _feature(approved_store)
    lowered = invoke(
        "update",
        {
            "slug": IMPLEMENTER,
            "set": ["budget={max_chars: 3000, max_docs: 20}"],
            "expect": _etag(approved_store, IMPLEMENTER),
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(lowered), lowered

    result = _brief(approved_store, item, actor="cto")

    assert is_ok(result), result
    assert f"You are acting as: {IMPLEMENTER}" in result["value"]["text"]
    assert len(result["value"]["text"]) <= 3000
    cto = invoke("show", {"slug": "cto"}, store=approved_store, actor="human")
    assert cto["value"]["doc"]["fields"]["budget"] == {"max_chars": 12000, "max_docs": 20}


def test_a_missing_pin_is_named_not_raised(approved_store: Path) -> None:
    item = _feature(approved_store)
    (approved_store / LEAN_TESTING_NOTE).unlink()

    result = _brief(approved_store, item)

    assert is_ok(result), result
    value = result["value"]
    assert "missing (run rail ops upgrade)" in value["text"]
    assert value["acting"]["skills"][0]["missing"] is True


def test_role_mode_brief_without_a_seat_names_the_role(role_store: SimpleNamespace) -> None:
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


def test_a_tiny_budget_collapses_skills_first(approved_store: Path) -> None:
    item = _feature(approved_store)

    result = _brief(approved_store, item, max_chars=600)

    assert is_ok(result), result
    text = result["value"]["text"]
    assert len(text) <= 600
    assert "(read: rail read show" in text
    assert "## objective" in text
    assert "Choose the smallest check that proves the change" not in text


def test_body_headings_never_collide_with_sections(approved_store: Path) -> None:
    item = _feature(approved_store)
    _replace_body(
        approved_store / LEAN_TESTING_NOTE,
        "### Lean testing\n\nChoose the smallest check that proves the change.\n\n"
        "## Heading\n\nBody text under a heading.\n",
    )

    result = _brief(approved_store, item)

    assert is_ok(result), result
    text = result["value"]["text"]
    headings = tuple(line[3:] for line in text.splitlines() if line.startswith("## "))
    assert headings == WORK_BRIEF_SECTIONS
    assert "#### Heading" in text


def test_the_brief_no_longer_prescribes_test_first(approved_store: Path) -> None:
    item = _feature(approved_store)

    result = _brief(approved_store, item)

    assert is_ok(result), result
    assert "failing test" not in result["value"]["text"]
