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


def _result(**overrides):
    value = {
        "exit": 0,
        "timed_out": False,
        "duration_s": 0.0,
        "stdout": "",
        "stderr": "",
        "omitted_bytes": 0,
        "digest": "sha256:0000",
    }
    value.update(overrides)
    return value


def test_a_timed_out_body_merge_is_refused(tmp_path, monkeypatch) -> None:
    import pytest

    import context_rail.runner.body_merge as body_merge
    from context_rail.store.write import WriteRefused

    monkeypatch.setattr(
        body_merge, "run", lambda *a, **k: _result(timed_out=True, exit=None)
    )

    with pytest.raises(WriteRefused) as caught:
        body_merge.merge_body("a\n", "b\n", "c\n", scratch=tmp_path)

    assert caught.value.code == "INTEGRATION_FAILURE"
    assert caught.value.details.get("process") == "git"


def test_an_omitted_body_merge_output_is_refused(tmp_path, monkeypatch) -> None:
    import pytest

    import context_rail.runner.body_merge as body_merge
    from context_rail.store.write import WriteRefused

    monkeypatch.setattr(body_merge, "run", lambda *a, **k: _result(omitted_bytes=1))

    with pytest.raises(WriteRefused) as caught:
        body_merge.merge_body("a\n", "b\n", "c\n", scratch=tmp_path)

    assert caught.value.code == "INTEGRATION_FAILURE"


def test_a_hard_body_merge_failure_is_refused(tmp_path, monkeypatch) -> None:
    import pytest

    import context_rail.runner.body_merge as body_merge
    from context_rail.store.write import WriteRefused

    monkeypatch.setattr(body_merge, "run", lambda *a, **k: _result(exit=128))

    with pytest.raises(WriteRefused) as caught:
        body_merge.merge_body("a\n", "b\n", "c\n", scratch=tmp_path)

    assert caught.value.code == "INTEGRATION_FAILURE"
