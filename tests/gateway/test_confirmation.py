"""The one shared typed-confirmation phrase, exact string only."""

from __future__ import annotations

import pytest

from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation


def test_phrases_are_exact() -> None:
    assert confirmation_phrase("approve", "charter") == "APPROVE charter"
    assert confirmation_phrase("pass", "unit") == "PASS unit"
    assert confirmation_phrase("fail", "unit") == "FAIL unit"
    assert confirmation_phrase("pack_use", "basic") == "USE PACK basic"
    assert confirmation_phrase("adapters_force", "adapters") == "FORCE ADAPTERS adapters"


def test_validation_accepts_only_the_exact_phrase() -> None:
    assert validate_confirmation("approve", "charter", "APPROVE charter") is None
    assert validate_confirmation("pass", "smoke", "PASS smoke") is None
    assert validate_confirmation("fail", "smoke", "FAIL smoke") is None
    assert validate_confirmation("pack_use", "basic", "USE PACK basic") is None
    assert validate_confirmation("adapters_force", "adapters", "FORCE ADAPTERS adapters") is None


@pytest.mark.parametrize(
    "supplied",
    [
        "approve charter",
        "APPROVE  charter",
        "APPROVE chart",
        "APPROVE charter ",
        "APPROVE charter.",
        "",
        "yes",
        True,
        None,
        1,
    ],
)
def test_validation_refuses_everything_else(supplied: object) -> None:
    reason = validate_confirmation("approve", "charter", supplied)  # type: ignore[arg-type]
    assert isinstance(reason, str) and reason


def test_phrase_is_bound_to_its_target() -> None:
    assert confirmation_phrase("approve", "smoke") == "APPROVE smoke"
    assert validate_confirmation("approve", "smoke", "APPROVE charter") is not None
    assert validate_confirmation("pass", "smoke", "FAIL smoke") is not None


def test_unknown_action_and_empty_target_are_refused() -> None:
    from context_rail.model.errors import ModelError

    with pytest.raises(ModelError):
        confirmation_phrase("deploy", "charter")
    with pytest.raises(ModelError):
        confirmation_phrase("approve", "")
