from __future__ import annotations

import pytest

from context_rail.kernel.merge import ABSENT, merge_frontmatter, merge_set


@pytest.mark.parametrize(("base", "ours", "theirs", "expected"), [
    ({"title": "a"}, {"title": "b"}, {"title": "a"}, {"title": "b"}),
    ({"title": "a"}, {"title": "a"}, {"title": "c"}, {"title": "c"}),
    ({"title": "a"}, {"title": "b"}, {"title": "b"}, {"title": "b"}),
])
def test_three_way_metadata_order(base, ours, theirs, expected) -> None:
    result = merge_frontmatter(base, ours, theirs)
    assert result.conflicts == () and result.merged == expected


def test_status_changed_differently_conflicts_even_within_one_category() -> None:
    result = merge_frontmatter({"status": "todo"}, {"status": "doing"}, {"status": "blocked"})
    assert [c.reason for c in result.conflicts] == ["status_divergent"]


def test_created_is_earliest_and_updated_is_latest() -> None:
    base = {"created": "2026-10-28T00:00:00Z", "updated": "2026-10-28T00:00:00Z"}
    ours = {"created": "2026-10-27T00:00:00Z", "updated": "2026-10-28T01:00:00Z"}
    theirs = {"created": "2026-10-28T00:00:00Z", "updated": "2026-10-28T02:00:00Z"}
    result = merge_frontmatter(base, ours, theirs)
    assert result.merged["created"] == "2026-10-27T00:00:00Z"
    assert result.merged["updated"] == "2026-10-28T02:00:00Z"


def test_invalid_timestamp_conflicts_without_string_comparison() -> None:
    result = merge_frontmatter({"updated": "x"}, {"updated": "not-a-date"}, {"updated": "2026-10-28T02:00:00Z"})
    assert [c.reason for c in result.conflicts] == ["invalid_timestamp"]


def test_tag_sets_merge_independent_additions_and_removals() -> None:
    assert merge_set(["a", "b"], ["a", "b", "c"], ["a"]) == ["a", "c"]


def test_deletion_against_modification_conflicts() -> None:
    result = merge_frontmatter({"tags": ["a"]}, {"tags": ABSENT}, {"tags": ["a", "b"]})
    assert [c.reason for c in result.conflicts] == ["deleted_vs_modified"]


def test_duplicate_or_non_string_list_elements_are_rejected_before_merge() -> None:
    result = merge_frontmatter({"tags": ["a"]}, {"tags": ["a", "a"]}, {"tags": ["a", 3]})
    assert [c.reason for c in result.conflicts] == ["malformed"]


def test_scalar_relation_replaced_differently_conflicts() -> None:
    result = merge_frontmatter({"part_of": "g1"}, {"part_of": "g2"}, {"part_of": "g3"})
    assert [c.reason for c in result.conflicts] == ["scalar_relation"]


def test_absent_is_distinct_from_null() -> None:
    result = merge_frontmatter({"owner": "x"}, {"owner": None}, {"owner": ABSENT})
    assert result.conflicts != ()
