"""The pure frontmatter transforms the rename walker is built from."""

from __future__ import annotations

import pytest

from context_rail.model.primitives import RELATIONS
from context_rail.store.frontmatter_edit import (
    join_frontmatter,
    retarget_text,
    retarget_wikilink,
    rewrite_links_block,
    split_frontmatter,
)

BLOCK = (
    "primitive: item\n"
    "preset: task\n"
    "title: Login\n"
    "part_of:\n"
    "- '[[auth]]'\n"
    "depends_on: '[[auth|the goal]]'\n"
    "summary: '[[auth]] stays text here'\n"
)


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("auth", "authentication"),
        ("auth|the goal", "authentication|the goal"),
        ("auth#Done", "authentication#Done"),
        ("goals/auth", "goals/authentication"),
        ("auth.md", "authentication.md"),
        ("auth.md#Done|the goal", "authentication.md#Done|the goal"),
        ("Auth", None),
        ("author", None),
        ("", None),
    ],
)
def test_retarget_wikilink_preserves_alias_anchor_prefix_and_extension(
    inner: str, expected: str | None
) -> None:
    assert retarget_wikilink(inner, "auth", "authentication") == expected


def test_retarget_text_counts_every_wikilink() -> None:
    text = "See [[auth]], [[auth|label]] and [[auth.md#x]] but not [[Auth]]."

    rewritten, count = retarget_text(text, "auth", "authentication")

    assert count == 3
    assert rewritten == (
        "See [[authentication]], [[authentication|label]] "
        "and [[authentication.md#x]] but not [[Auth]]."
    )


def test_retarget_text_leaves_other_text_byte_identical() -> None:
    text = "# Title\n\nNothing to see.\n"

    assert retarget_text(text, "auth", "authentication") == (text, 0)


def test_rewrite_links_block_only_touches_declared_link_fields() -> None:
    updated, count, handled = rewrite_links_block(
        BLOCK, "auth", "authentication", set(RELATIONS)
    )

    assert handled is True
    assert count == 2
    assert "[[authentication]]" in updated
    assert "[[authentication|the goal]]" in updated
    assert "summary: '[[auth]] stays text here'" in updated


def test_rewrite_links_block_reports_unsupported_flow_syntax() -> None:
    block = "part_of: ['[[auth]]']\n"

    updated, count, handled = rewrite_links_block(
        block, "auth", "authentication", set(RELATIONS)
    )

    assert handled is False
    assert count == 0
    assert updated == block


def test_split_and_join_frontmatter_round_trip() -> None:
    text = "---\ntitle: Login\n---\n# Login\n"

    split = split_frontmatter(text)

    assert split == ("title: Login", "# Login\n")
    assert join_frontmatter(*split) == text
    assert split_frontmatter("# no frontmatter\n") is None
