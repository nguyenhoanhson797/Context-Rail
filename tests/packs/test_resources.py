from __future__ import annotations

from importlib import resources

import pytest

from context_rail.model.pack import load_pack
from helpers import init_store

PACKS = ("indie", "scrum", "waterfall")
PRIMITIVE_TEMPLATES = ("item", "note", "record", "check")
STAGE_1B_TEMPLATES = ("charter", "goal", "vision", "scope", "wont", "work-order")
ROLES = ("cto", "implementer", "reviewer")
GOVERNANCE = ("charter", "vision", "wont", "skill", "cto", "profile", "convention", "decision", "event", "check")


def _pack_dir(name: str):
    return resources.files("context_rail") / "packs" / name


@pytest.mark.parametrize("name", PACKS)
def test_builtin_pack_ships_every_declared_resource(name: str) -> None:
    root = _pack_dir(name)
    missing = [t for t in PRIMITIVE_TEMPLATES + STAGE_1B_TEMPLATES if not (root / "templates" / f"{t}.md").is_file()]
    missing += [r for r in ROLES if not (root / "profiles" / f"{r}.md").is_file()]
    missing += [s for s in ("test-first", "review-checklist") if not (root / "skills" / s / "SKILL.md").is_file()]
    assert missing == []


def test_small_pack_is_unchanged_and_custom_store_override_wins(tmp_path) -> None:
    store = init_store(tmp_path)
    assert load_pack("small", store).name == "small"


def test_governance_presets_keep_their_primitive_across_packs(tmp_path) -> None:
    store = init_store(tmp_path)
    small = load_pack("small", store)
    for name in PACKS:
        pack = load_pack(name, store)
        for preset in GOVERNANCE:
            assert pack.presets[preset].primitive == small.presets[preset].primitive


def test_profile_parameters_match_stage_1b(tmp_path) -> None:
    from context_rail.store.doc import parse_doc

    def params(root, role: str) -> dict:
        data = (root / "profiles" / f"{role}.md").read_bytes()
        profile = parse_doc(f"profiles/{role}.md", data).fields
        return {k: profile.get(k) for k in ("model", "effort", "auto", "tools", "budgets")}

    base = _pack_dir("small")
    for name in PACKS:
        for role in ROLES:
            assert params(_pack_dir(name), role) == params(base, role)
