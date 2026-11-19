"""A profile names methods, a context budget and skills; the tool running the agent owns the rest."""

from __future__ import annotations

from importlib import resources
from typing import Any

import pytest

from context_rail.core.util import load_yaml
from context_rail.model.cto import PROFILE_METHODS, REMOVED_PROFILE_KEYS, parse_profile
from context_rail.model.errors import ModelError

PACKS = ("adaptive-contract", "basic", "flow", "portfolio", "scrum", "video", "waterfall")
FORBIDDEN_WORDS = ("droid", "rail-report", "parent writes", "worktree")
REMOVED_COMMANDS = ("dispatch", "merge", "tick", "loop dispatch", "loop merge", "loop tick")


def fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "version": 1,
        "allowed_methods": ["find", "show", "cto brief", "ledger", "report"],
        "recipe": "implementer",
        "budget": {"max_chars": 12000, "max_docs": 20},
        "skills": ["workspace/skills/test-first.md"],
        "budgets": {"attempts": 2, "seconds": 3600, "tokens": None, "credits": None},
    }
    base.update(over)
    return base


@pytest.mark.parametrize("key", REMOVED_PROFILE_KEYS)
def test_profile_removed_keys_refused_with_upgrade_hint(key: str) -> None:
    with pytest.raises(ModelError) as raised:
        parse_profile(fields(**{key: "x"}))

    assert "rail ops upgrade" in raised.value.message
    assert repr(key) in raised.value.message


def test_removed_keys_are_the_seven_runtime_fields() -> None:
    assert set(REMOVED_PROFILE_KEYS) == {
        "model",
        "effort",
        "auto",
        "tools",
        "runner",
        "runner_models",
        "runner_options",
    }


def test_profile_methods_exclude_removed_commands() -> None:
    assert not set(REMOVED_COMMANDS) & set(PROFILE_METHODS)
    with pytest.raises(ModelError):
        parse_profile(fields(allowed_methods=["dispatch"]))


def test_profile_methods_name_the_loop_reads_and_records() -> None:
    parsed = parse_profile(
        fields(allowed_methods=["brief", "candidate", "review", "verify", "report"])
    )

    assert parsed.allowed_methods == ("brief", "candidate", "review", "verify", "report")


def shipped_profiles() -> list[tuple[str, str, str]]:
    found = []
    for pack in PACKS:
        folder = resources.files("context_rail").joinpath("packs", pack, "profiles")
        for role in ("cto", "implementer", "reviewer"):
            found.append((pack, role, folder.joinpath(f"{role}.md").read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize(("pack", "role", "text"), shipped_profiles())
def test_shipped_profiles_parse_and_name_no_runtime(pack: str, role: str, text: str) -> None:
    front, body = text.replace("\r\n", "\n").split("\n---\n", 1)[0].removeprefix("---\n"), text.replace("\r\n", "\n").split("\n---\n", 1)[1]
    profile = parse_profile(load_yaml(front), body=body)

    assert profile.recipe == role
    assert "brief" in profile.allowed_methods
    lowered = body.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, (pack, role, word)
    assert "rail loop brief" in body or role == "cto"


def test_shipped_profiles_cover_every_pack_and_role() -> None:
    assert len(shipped_profiles()) == len(PACKS) * 3


def test_skill_allowed_tools_is_free_form(tmp_path: Any) -> None:
    from context_rail.model.cto import _check_skill

    _check_skill(
        "review",
        {
            "schema": 1,
            "version": 1,
            "name": "review",
            "description": "d",
            "allowed_tools": ["Bash(git:*)", "Read"],
        },
        "body",
    )


def test_config_options_has_no_runtime_vocabularies() -> None:
    from context_rail.ui.readmodels import _config_options

    options = _config_options(None)

    assert set(options) == {"status", "methods"}