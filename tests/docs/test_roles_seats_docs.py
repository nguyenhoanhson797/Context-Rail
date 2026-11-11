"""Public docs cover roles, seats, sessions and the compat rule."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_COMMAND_REFS = (
    "docs/agent-onboarding/references/commands.md",
    "skills/context-rail-v3/references/commands.md",
)
_SEAT_COMMANDS = (
    "rail seat list",
    "rail seat grant",
    "rail seat revoke",
    "rail seat open",
    "rail seat close",
    "rail seat show",
)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_every_seat_command_is_in_the_command_reference() -> None:
    for rel in _COMMAND_REFS:
        text = _read(rel)
        for command in _SEAT_COMMANDS:
            assert command in text, (rel, command)
        assert "rail <seat>" in text, rel
        assert "RAIL_SESSION" in text, rel


def test_the_public_entry_points_explain_role_mode() -> None:
    for rel in ("README.md", "docs/agent-onboarding/README.md"):
        text = _read(rel)
        for phrase in (
            "role",
            "seat",
            "rail seat grant",
            "rail seat open",
            "RAIL_SESSION",
            "human",
        ):
            assert phrase in text, (rel, phrase)


def test_pack_create_roles_is_documented() -> None:
    for rel in (
        *_COMMAND_REFS,
        "skills/context-rail-v3-pack-authoring/SKILL.md",
    ):
        assert "pack create --roles" in _read(rel), rel


def test_the_main_skill_routes_role_mode() -> None:
    text = _read("skills/context-rail-v3/SKILL.md")

    for phrase in ("seat grant", "seat open", "RAIL_SESSION", "role mode"):
        assert phrase in text, phrase


def test_public_docs_never_reference_private_working_material() -> None:
    public = [
        _ROOT / "README.md",
        *(_ROOT / "docs" / "agent-onboarding").rglob("*.md"),
        *(_ROOT / "skills").rglob("*.md"),
    ]
    for path in public:
        assert "docs/superpowers/" not in path.read_text(encoding="utf-8"), path
