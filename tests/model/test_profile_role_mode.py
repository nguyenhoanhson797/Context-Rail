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
