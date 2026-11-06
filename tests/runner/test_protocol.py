"""The runner a launch resolves to: override, then environment, then profile."""

from __future__ import annotations

import pytest

from context_rail.model.cto import Profile
from context_rail.runner.protocol import runner_for_profile, runner_name
from context_rail.store.write import WriteRefused

_RECIPE = "implementer"


def _profile(runner: str = "droid") -> Profile:
    return Profile(
        schema=1,
        version=1,
        allowed_methods=(),
        recipe=_RECIPE,
        budget=None,
        model="model-1",
        effort=None,
        auto=None,
        tools=("read-cli",),
        skills=(),
        budgets={},
        body="body",
        runner=runner,
        runner_options={},
    )


def test_the_profile_declares_the_default_runner() -> None:
    assert runner_name(_profile("droid")) == "droid"
    assert runner_for_profile(_profile("claude")).name == "claude"


def test_an_explicit_override_wins_over_the_profile() -> None:
    assert runner_name(_profile("droid"), "codex") == "codex"
    assert runner_for_profile(_profile("claude"), "local").name == "local"


def test_the_environment_overrides_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIL_RUNNER", "claude")
    assert runner_name(_profile("droid")) == "claude"


def test_an_explicit_override_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAIL_RUNNER", "claude")
    assert runner_name(_profile("droid"), "codex") == "codex"


def test_an_unknown_runner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(WriteRefused) as refused:
        runner_name(_profile("droid"), "made-up")
    assert refused.value.code == "VALIDATION_FAILED"

    monkeypatch.setenv("RAIL_RUNNER", "made-up")
    with pytest.raises(WriteRefused):
        runner_name(_profile("droid"))
