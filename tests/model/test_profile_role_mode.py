"""Role-mode profiles may leave out the recipe and the method list."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.gateway import Context
from context_rail.model.cto import parse_profile
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.runner import protocol
from context_rail.runner.droid import droid_env, droid_profile


def fields(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "version": 1,
        "budget": {"max_chars": 8000, "max_docs": 20},
        "model": "model-x",
        "tools": ["read-cli"],
        "skills": [],
        "budgets": {"attempts": 2, "seconds": 600, "tokens": None, "credits": None},
    }
    base.update(extra)
    return base


def legacy(**extra: Any) -> dict[str, Any]:
    return fields(recipe="implementer", allowed_methods=["find", "show"], **extra)


def test_role_mode_profile_without_recipe_or_methods_parses() -> None:
    profile = parse_profile(fields(), body="You work.", role_mode=True)

    assert profile.recipe is None
    assert profile.allowed_methods == ()
    assert profile.tools == ("read-cli",)


def test_role_mode_profile_with_recipe_and_methods_keeps_them() -> None:
    profile = parse_profile(legacy(), body="You work.", role_mode=True)

    assert profile.recipe == "implementer"
    assert profile.allowed_methods == ("find", "show")


def test_role_mode_still_validates_what_it_is_given() -> None:
    with pytest.raises(ModelError, match="recipe"):
        parse_profile(fields(recipe="boss"), role_mode=True)
    with pytest.raises(ModelError, match="allowed_methods"):
        parse_profile(fields(allowed_methods=["no such method"]), role_mode=True)


def test_legacy_profile_still_requires_recipe_and_methods() -> None:
    with pytest.raises(ModelError, match="recipe") as missing_recipe:
        parse_profile(fields(allowed_methods=["find"]))
    with pytest.raises(ModelError, match="allowed_methods") as missing_methods:
        parse_profile(fields(recipe="implementer"))

    assert missing_recipe.value.code == "VALIDATION_FAILED"
    assert missing_methods.value.code == "VALIDATION_FAILED"
    with pytest.raises(ModelError, match="recipe"):
        parse_profile(fields(), role_mode=False)


def test_legacy_auto_high_still_needs_the_cto_recipe() -> None:
    with pytest.raises(ModelError, match="auto high"):
        parse_profile(legacy(auto="high"))
    cto = fields(recipe="cto", allowed_methods=["find"], auto="high")

    assert parse_profile(cto).auto == "high"


def test_role_mode_auto_high_parses() -> None:
    assert parse_profile(fields(auto="high"), role_mode=True).auto == "high"
    assert parse_profile(legacy(auto="high"), role_mode=True).auto == "high"


def test_a_reviewer_recipe_is_still_read_only_in_role_mode() -> None:
    raw = fields(recipe="reviewer", tools=["edit-cli"])

    with pytest.raises(ModelError, match="read-only"):
        parse_profile(raw, role_mode=True)


def test_droid_env_unit_handles_a_none_recipe() -> None:
    profile = parse_profile(fields(), role_mode=True)

    launch = droid_profile(profile)

    assert launch.name == "worker"
    assert droid_profile(profile, name="seat-1").name == "seat-1"
    assert droid_env(launch.name, "droid")["RAIL_ACTOR"] == "worker"


class _Launched(Exception):
    def __init__(self, extra_env: dict[str, str]) -> None:
        self.extra_env = extra_env


def _launch_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: dict[str, Any]
) -> dict[str, str]:
    def capture(*_args: Any, **kwargs: Any) -> Any:
        raise _Launched(kwargs["extra_env"])

    monkeypatch.setattr(protocol, "_run", capture)
    role = tmp_path / "role.md"
    order = tmp_path / "order.md"
    role.write_text("role", encoding="utf-8")
    order.write_text("order", encoding="utf-8")
    profile = parse_profile(raw, body="You work.", role_mode=True)
    with pytest.raises(_Launched) as launched:
        protocol.run_runner(
            profile=profile,
            worktree=tmp_path,
            confine_to=tmp_path,
            role_file=role,
            order_file=order,
            timeout=5,
        )
    return launched.value.extra_env


def test_a_runner_launch_names_no_actor_for_a_none_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    named = _launch_env(monkeypatch, tmp_path, legacy())
    bare = _launch_env(monkeypatch, tmp_path, fields())

    assert named["RAIL_ACTOR"] == "implementer"
    assert "RAIL_ACTOR" not in bare
    assert "RAIL_DROID_BIN" in bare


def _context(store: Path, pack: str) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor="human",
    )


def test_context_role_mode_is_true_with_roles(role_store: SimpleNamespace) -> None:
    assert _context(role_store.store, "scrum").role_mode is True


def test_context_role_mode_is_false_without_roles(approved_store: Path) -> None:
    assert _context(approved_store, "basic").role_mode is False
