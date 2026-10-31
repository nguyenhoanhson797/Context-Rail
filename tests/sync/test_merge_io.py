from __future__ import annotations

from context_rail.runner.body_merge import merge_body


def test_disjoint_body_edits_merge(tmp_path) -> None:
    base = "one\n\ntwo\n\nthree\n"
    result = merge_body(base, "ONE\n\ntwo\n\nthree\n", "one\n\ntwo\n\nTHREE\n", scratch=tmp_path)
    assert result.conflicted is False and result.text == "ONE\n\ntwo\n\nTHREE\n"


def test_overlapping_body_edits_return_a_conflict_never_a_document(tmp_path) -> None:
    result = merge_body("same\n", "ours\n", "theirs\n", scratch=tmp_path)
    assert result.conflicted is True and result.text is None


def test_frontmatter_bytes_never_reach_merge_file(tmp_path) -> None:
    result = merge_body("body\n", "body edit\n", "body\n", scratch=tmp_path)
    assert "---" not in (tmp_path / "base.body").read_text(encoding="utf-8")
