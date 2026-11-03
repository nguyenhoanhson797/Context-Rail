"""The pack-authoring skill exists, is discoverable and names the real surface."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"
SKILL = SKILLS / "context-rail-v3-pack-authoring" / "SKILL.md"
NAME = "context-rail-v3-pack-authoring"


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    data = yaml.safe_load(block)
    assert isinstance(data, dict)
    return data


def test_the_skill_exists_with_a_description_that_names_authoring() -> None:
    assert SKILL.is_file()
    front = _frontmatter(SKILL)
    assert front["name"] == NAME
    description = str(front["description"]).lower()
    assert "pack" in description
    assert any(word in description for word in ("author", "create", "adjust", "change"))


def test_the_body_names_every_placement_and_metadata_feature() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for needle in (
        "{part_of}",
        "{about}",
        "{verifies}",
        "{nearest:",
        "{root:",
        "{field:",
        "{kind}",
        "kind:",
        "required",
        "defaults",
        "pack validate",
        "doctor",
        "repath",
    ):
        assert needle in text, f"the skill never names {needle!r}"


def test_the_skills_readme_and_the_router_point_at_it() -> None:
    readme = (SKILLS / "README.md").read_text(encoding="utf-8")
    assert NAME in readme
    router = (SKILLS / "context-rail-v3" / "SKILL.md").read_text(encoding="utf-8")
    assert NAME in router
