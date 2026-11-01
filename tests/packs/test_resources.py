from __future__ import annotations

from importlib import resources

import pytest

from context_rail.model.pack import load_pack
from helpers import init_store

PACKS = ("basic", "flow", "scrum", "waterfall")
PRIMITIVE_TEMPLATES = ("item", "note", "record", "check")
STAGE_1B_TEMPLATES = ("charter", "goal", "vision", "scope", "wont", "work-order")
ROLES = ("cto", "implementer", "reviewer")
GOVERNANCE = ("charter", "vision", "wont", "skill", "cto", "profile", "convention", "decision", "event", "check")
BASE_PRESETS = GOVERNANCE + (
    "goal",
    "scope",
    "task",
    "bug",
    "note",
    "fix",
    "risk",
    "question",
)


def _pack_dir(name: str):
    return resources.files("context_rail") / "packs" / name


@pytest.mark.parametrize("name", PACKS)
def test_builtin_pack_ships_every_declared_resource(name: str) -> None:
    root = _pack_dir(name)
    missing = [t for t in PRIMITIVE_TEMPLATES + STAGE_1B_TEMPLATES if not (root / "templates" / f"{t}.md").is_file()]
    missing += [r for r in ROLES if not (root / "profiles" / f"{r}.md").is_file()]
    missing += [s for s in ("test-first", "review-checklist") if not (root / "skills" / s / "SKILL.md").is_file()]
    assert missing == []


def test_builtin_pack_names_match_their_directories(tmp_path) -> None:
    store = init_store(tmp_path)
    for name in PACKS:
        assert load_pack(name, store).name == name


@pytest.mark.parametrize("name", PACKS)
def test_every_pack_declares_the_base_presets(tmp_path, name: str) -> None:
    pack = load_pack(name, tmp_path)
    assert set(BASE_PRESETS) <= set(pack.presets)


@pytest.mark.parametrize("name", PACKS)
def test_every_pack_has_a_complete_transition_table(tmp_path, name: str) -> None:
    pack = load_pack(name, tmp_path)
    assert set(pack.transitions) == set(pack.statuses)
    for source, targets in pack.transitions.items():
        assert targets, f"{name}: {source} has no way out"
        for target in targets:
            assert target in pack.statuses


def test_a_store_on_the_deprecated_name_reads_the_successor_pack(tmp_path) -> None:
    store = init_store(tmp_path, pack="small")
    small = load_pack("small", store)
    basic = load_pack("basic", store)
    assert small.name == "small"
    assert set(small.presets) == set(basic.presets)
    assert small.statuses == basic.statuses
    assert small.policy == basic.policy


def test_governance_presets_keep_their_primitive_across_packs(tmp_path) -> None:
    store = init_store(tmp_path)
    basic = load_pack("basic", store)
    for name in PACKS:
        pack = load_pack(name, store)
        for preset in GOVERNANCE:
            assert pack.presets[preset].primitive == basic.presets[preset].primitive


def test_profile_parameters_match_stage_1b(tmp_path) -> None:
    from context_rail.store.doc import parse_doc

    def params(root, role: str) -> dict:
        data = (root / "profiles" / f"{role}.md").read_bytes()
        profile = parse_doc(f"profiles/{role}.md", data).fields
        return {k: profile.get(k) for k in ("model", "effort", "auto", "tools", "budgets")}

    base = _pack_dir("basic")
    for name in PACKS:
        for role in ROLES:
            assert params(_pack_dir(name), role) == params(base, role)
