"""Shipped pack skills and profiles: pins resolve, names and commands are real."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path, PurePosixPath

import pytest
from command_names import unknown_rail_commands
from helpers import init_store

from context_rail.gateway.handlers.onboarding import agent_ops
from context_rail.model.cto import (
    REPORT_TARGET_CLASSES,
    load_pack_profile,
    load_pack_skills,
    load_skill_sources,
)
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack

PACKS = ("basic", "flow", "scrum", "video", "waterfall")
ROLES = ("cto", "implementer", "reviewer")
REPORT_FIELDS = (
    "schema",
    "target_class",
    "target",
    "what_happened",
    "expected",
    "observed",
    "impact",
    "evidence",
    "nothing_to_report",
)
_DO = re.compile(r"`rail do ([^`\s]+)")


def _pack_root(pack: str):
    return resources.files("context_rail").joinpath("packs", pack)


@pytest.mark.parametrize("pack", PACKS)
def test_every_builtin_profile_pin_resolves(tmp_path: Path, pack: str) -> None:
    sources = load_skill_sources(pack)
    for role in ROLES:
        for declared in load_pack_profile(pack, role).skills:
            assert PurePosixPath(declared).stem in sources, (pack, role, declared)

    store = init_store(tmp_path, pack=pack)
    written = {op.path for op in agent_ops(store, load_manifest(store), pack)}
    for role in ROLES:
        for declared in load_pack_profile(pack, role).skills:
            assert declared in written, (pack, role, declared)


@pytest.mark.parametrize("pack", PACKS)
def test_no_pack_ships_or_pins_test_first(pack: str) -> None:
    assert not (_pack_root(pack) / "skills" / "test-first").exists()
    for role in ROLES:
        profile = load_pack_profile(pack, role)
        assert not [p for p in profile.skills if PurePosixPath(p).stem == "test-first"]
        assert "test first" not in profile.body.lower(), (pack, role)


@pytest.mark.parametrize("pack", PACKS)
def test_pack_skills_name_only_declared_actions(tmp_path: Path, pack: str) -> None:
    declared = set(load_pack(pack, tmp_path).actions)
    for name, text in load_pack_skills(pack).items():
        named = set(_DO.findall(text))
        assert named <= declared, (pack, name, sorted(named - declared))


@pytest.mark.parametrize("pack", PACKS)
def test_pack_skill_commands_resolve(pack: str) -> None:
    for name, text in load_pack_skills(pack).items():
        assert unknown_rail_commands(text) == [], (pack, name)
    for role in ROLES:
        assert unknown_rail_commands(load_pack_profile(pack, role).body) == [], (pack, role)


@pytest.mark.parametrize("pack", PACKS)
def test_no_pack_ships_a_work_order_template(pack: str) -> None:
    assert not (_pack_root(pack) / "templates" / "work-order.md").is_file()


@pytest.mark.parametrize("pack", PACKS)
def test_the_cto_names_how_to_propose_a_change(pack: str) -> None:
    body = load_pack_profile(pack, "cto").body
    assert "rail loop report" in body and "--report-file" in body
    for field in REPORT_FIELDS:
        assert f"`{field}`" in body, (pack, field)
    for target_class in REPORT_TARGET_CLASSES:
        assert f"`{target_class}`" in body, (pack, target_class)
    assert "`human-wait`" in body and "set to `human-wait`" in body
    assert "rail ops rollback" in body and "human-only" in body
