"""A candidate record names one commit an agent produced for one item."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.candidate import (
    CANDIDATE_KIND,
    DIFF_TEXT_CAP,
    FILES_CAP,
    parse_candidate,
    parse_usage_map,
)
from context_rail.model.errors import ModelError

SHA = "a" * 40
BASE = "b" * 40


def _payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": 1,
        "item": "login-page",
        "commit": SHA,
        "base": BASE,
        "files_changed": ["src/app.py"],
        "attempt": 1,
        "actor": "dev",
        "at": "2026-11-12T10:00:00Z",
    }
    payload.update(changes)
    return payload


def test_candidate_kind_is_stable() -> None:
    assert CANDIDATE_KIND == "candidate.recorded"


def test_parse_candidate_accepts_minimal_payload() -> None:
    candidate = parse_candidate(_payload())

    assert candidate.item == "login-page"
    assert candidate.commit == SHA
    assert candidate.base == BASE
    assert candidate.files_changed == ("src/app.py",)
    assert candidate.attempt == 1
    assert candidate.actor == "dev"
    assert candidate.files_truncated is False
    assert candidate.diff is None
    assert candidate.diff_truncated is False
    assert candidate.usage == {}
    assert candidate.skills == ()
    assert candidate.seat is None and candidate.model is None
    assert candidate.harness is None and candidate.duration_s is None


def test_parse_candidate_accepts_every_optional_field() -> None:
    candidate = parse_candidate(
        _payload(
            files_truncated=True,
            diff="diff --git a/app.py b/app.py\n+x = 1\n",
            diff_truncated=True,
            seat="impl-1",
            seat_role="implementer",
            profile="implementer",
            profile_version=3,
            skills=[{"identity": "skills/review", "version": 2, "etag": "abc"}],
            model="any-model-name",
            harness="opencode",
            usage={"input_tokens": 12, "cost_usd": 0.5},
            duration_s=3.5,
        )
    )

    assert candidate.files_truncated is True
    assert candidate.diff == "diff --git a/app.py b/app.py\n+x = 1\n"
    assert candidate.diff_truncated is True
    assert candidate.seat == "impl-1" and candidate.seat_role == "implementer"
    assert candidate.profile == "implementer" and candidate.profile_version == 3
    assert candidate.skills == ({"identity": "skills/review", "version": 2, "etag": "abc"},)
    assert candidate.model == "any-model-name" and candidate.harness == "opencode"
    assert candidate.usage == {"input_tokens": 12, "cost_usd": 0.5}
    assert candidate.duration_s == 3.5


def test_parse_candidate_refuses_unknown_key() -> None:
    with pytest.raises(ModelError, match="unknown key"):
        parse_candidate(_payload(runner="droid"))


def test_parse_candidate_refuses_missing_required() -> None:
    payload = _payload()
    del payload["commit"]

    with pytest.raises(ModelError, match="missing commit"):
        parse_candidate(payload)


@pytest.mark.parametrize("field", ["commit", "base"])
@pytest.mark.parametrize("value", ["abc1234", "G" * 40, "A" * 40, "a" * 41])
def test_parse_candidate_refuses_anything_but_a_full_sha(field: str, value: str) -> None:
    with pytest.raises(ModelError, match="40"):
        parse_candidate(_payload(**{field: value}))


def test_parse_candidate_refuses_more_than_500_files() -> None:
    files = [f"src/f{index}.py" for index in range(FILES_CAP + 1)]

    with pytest.raises(ModelError, match="500"):
        parse_candidate(_payload(files_changed=files))

    assert len(parse_candidate(_payload(files_changed=files[:FILES_CAP])).files_changed) == 500


@pytest.mark.parametrize("field", ["model", "harness"])
def test_parse_candidate_refuses_free_text_over_200_chars(field: str) -> None:
    with pytest.raises(ModelError, match="200"):
        parse_candidate(_payload(**{field: "m" * 201}))

    assert getattr(parse_candidate(_payload(**{field: "m" * 200})), field) == "m" * 200


def test_parse_candidate_refuses_bad_attempt_duration_and_time() -> None:
    for change in ({"attempt": 0}, {"duration_s": -1}, {"at": "yesterday"}):
        with pytest.raises(ModelError):
            parse_candidate(_payload(**change))


def test_parse_usage_map_accepts_numbers_and_unknown() -> None:
    raw = {"input_tokens": 12, "cost_usd": 0.5, "x": "unknown"}

    assert parse_usage_map(raw, "usage") == raw


def test_parse_usage_map_refuses_bad_key_negative_and_too_many() -> None:
    with pytest.raises(ModelError, match="Input-Tokens"):
        parse_usage_map({"Input-Tokens": 1}, "usage")
    with pytest.raises(ModelError, match="non-negative"):
        parse_usage_map({"input_tokens": -1}, "usage")
    with pytest.raises(ModelError, match="32"):
        parse_usage_map({f"k{index}": 1 for index in range(33)}, "usage")
    assert len(parse_usage_map({f"k{index}": 1 for index in range(32)}, "usage")) == 32


@pytest.mark.parametrize("value", [True, "12", None, float("nan"), float("inf")])
def test_parse_usage_map_refuses_values_that_are_not_counts(value: object) -> None:
    with pytest.raises(ModelError):
        parse_usage_map({"input_tokens": value}, "usage")


def test_parse_candidate_validates_diff_snapshot_bounds_and_types() -> None:
    empty = parse_candidate(_payload(diff="", diff_truncated=False))
    assert empty.diff == ""
    assert empty.diff_truncated is False

    exact = parse_candidate(_payload(diff="d" * DIFF_TEXT_CAP, diff_truncated=True))
    assert len(exact.diff or "") == DIFF_TEXT_CAP
    assert exact.diff_truncated is True

    with pytest.raises(ModelError, match="6000"):
        parse_candidate(_payload(diff="d" * (DIFF_TEXT_CAP + 1)))

    with pytest.raises(ModelError, match="string"):
        parse_candidate(_payload(diff=123))

    with pytest.raises(ModelError, match="true or false"):
        parse_candidate(_payload(diff_truncated="yes"))
