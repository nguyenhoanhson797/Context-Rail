"""The runner and model a launch resolves to: override, then environment, then profile."""

from __future__ import annotations

import pytest

from context_rail.model.cto import Profile
from context_rail.runner.protocol import (
    get_runner,
    resolve_model,
    runner_for_profile,
    runner_name,
)
from context_rail.store.write import WriteRefused

_RECIPE = "implementer"


def _profile(runner: str = "droid", models: dict[str, str] | None = None) -> Profile:
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
        runner_models=models or {},
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


def test_the_model_follows_the_resolved_runner() -> None:
    profile = _profile("droid", {"droid": "droid-model", "codex": "codex-model"})
    assert resolve_model(profile, "droid") == "droid-model"
    assert resolve_model(profile, "codex") == "codex-model"
    # No mapping for that adapter: the single declared model is the fallback.
    assert resolve_model(profile, "claude") == "model-1"


def test_an_environment_model_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIL_MODEL", "env-model")
    profile = _profile("droid", {"droid": "droid-model"})
    assert resolve_model(profile, "droid") == "env-model"


def test_a_launch_refuses_a_runner_with_no_model() -> None:
    profile = _profile("droid", {"droid": "droid-model"})
    empty = Profile(
        schema=profile.schema,
        version=profile.version,
        allowed_methods=profile.allowed_methods,
        recipe=profile.recipe,
        budget=profile.budget,
        model="",
        effort=None,
        auto=None,
        tools=profile.tools,
        skills=profile.skills,
        budgets=profile.budgets,
        body=profile.body,
        runner=profile.runner,
        runner_models={},
        runner_options={},
    )
    with pytest.raises(WriteRefused) as refused:
        resolve_model(empty, "codex")
    assert refused.value.code == "INTEGRATION_FAILURE"


def test_the_argv_carries_the_resolved_model() -> None:
    from pathlib import Path

    profile = _profile("codex", {"codex": "codex-model"})
    argv = get_runner("codex").build_argv(
        profile=profile,
        worktree=Path("."),
        role_file=Path("role.md"),
        order_file=Path("order.md"),
        model=resolve_model(profile, "codex"),
    )
    assert argv[argv.index("-m") + 1] == "codex-model"


@pytest.mark.parametrize("name", ["gemini", "opencode", "grok"])
def test_prompt_adapters_carry_the_model_and_one_combined_prompt(
    name: str, tmp_path
) -> None:
    role = tmp_path / "role.md"
    order = tmp_path / "order.md"
    role.write_text("ROLE", encoding="utf-8")
    order.write_text("ORDER", encoding="utf-8")
    profile = _profile(name, {name: f"{name}-model"})
    argv = get_runner(name).build_argv(
        profile=profile,
        worktree=tmp_path,
        role_file=role,
        order_file=order,
        model=resolve_model(profile, name),
    )
    assert argv[0] == name
    assert f"{name}-model" in argv
    assert any("ROLE" in arg and "ORDER" in arg for arg in argv)


def test_prompt_adapter_args_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json

    role = tmp_path / "role.md"
    order = tmp_path / "order.md"
    role.write_text("ROLE", encoding="utf-8")
    order.write_text("ORDER", encoding="utf-8")
    monkeypatch.setenv(
        "RAIL_GEMINI_ARGS",
        json.dumps(["gemini", "--model", "{model}", "-p", "{prompt}"]),
    )
    profile = _profile("gemini", {"gemini": "gemini-model"})
    argv = get_runner("gemini").build_argv(
        profile=profile,
        worktree=tmp_path,
        role_file=role,
        order_file=order,
        model=resolve_model(profile, "gemini"),
    )
    assert argv[:5] == ["gemini", "--model", "gemini-model", "-p", argv[4]]
    assert "ROLE" in argv[4]


def test_a_malformed_args_override_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    role = tmp_path / "role.md"
    order = tmp_path / "order.md"
    role.write_text("ROLE", encoding="utf-8")
    order.write_text("ORDER", encoding="utf-8")
    monkeypatch.setenv("RAIL_GEMINI_ARGS", "not-json")
    profile = _profile("gemini", {"gemini": "gemini-model"})
    with pytest.raises(WriteRefused) as refused:
        get_runner("gemini").build_argv(
            profile=profile,
            worktree=tmp_path,
            role_file=role,
            order_file=order,
        )
    assert refused.value.code == "VALIDATION_FAILED"
