"""The public docs describe the operating model and the upgrade path."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_readme_documents_the_operating_model() -> None:
    text = _read("README.md")

    for phrase in (
        "operating",
        "unit",
        "hierarchy",
        "focus",
        "queue",
        "progress",
        "surface.yaml",
        "rail model",
        "presentation",
        "approval_mismatch",
        "approve pack:",
        "neutral model",
    ):
        assert phrase in text, phrase


def test_readme_does_not_promise_a_pack_yaml_read() -> None:
    text = _read("README.md")

    assert "Agents never read `pack.yaml`" in text


def test_the_surface_skill_is_model_first() -> None:
    text = _read("skills/context-rail-v3-surface/SKILL.md")

    for phrase in (
        "rail model",
        "--focus-only",
        "--into",
        "AMBIGUOUS_FOCUS",
        "breadcrumb",
        "Never read `pack.yaml`",
    ):
        assert phrase in text, phrase


def test_the_pack_authoring_skill_documents_operating_and_surface() -> None:
    text = _read("skills/context-rail-v3-pack-authoring/SKILL.md")

    for phrase in (
        "operating",
        "surface.yaml",
        "semantic",
        "presentation",
        "rail --actor human approve pack:",
        "NO_FOCUS",
    ):
        assert phrase in text, phrase


def test_the_loop_skill_documents_focus_and_the_escapes() -> None:
    text = _read("skills/context-rail-v3-loop/SKILL.md")

    for phrase in ("Focus and the queue", "--all", "AMBIGUOUS_FOCUS", "--into"):
        assert phrase in text, phrase


def test_the_ops_skill_documents_digests_and_doctor_codes() -> None:
    text = _read("skills/context-rail-v3-ops/SKILL.md")

    for phrase in (
        "semantic_digest",
        "presentation_digest",
        "full_digest",
        "approve pack:",
        "focus_missing",
        "hierarchy_placement",
    ):
        assert phrase in text, phrase


def test_the_workflows_skill_keeps_operating_beside_workflows() -> None:
    text = _read("skills/context-rail-v3-workflows/SKILL.md")

    assert "The operating model sits beside the workflow" in text
    assert "never changes a transition" in text


def test_the_command_reference_documents_the_new_flags() -> None:
    text = _read("skills/context-rail-v3/references/commands.md")

    for phrase in ("rail model", "--focus-only", "NO_FOCUS", "semantic_digest"):
        assert phrase in text, phrase


def test_agents_md_records_the_v5_contract() -> None:
    text = _read("AGENTS.md")

    for phrase in (
        "Pack version 2",
        "`operating`",
        "`surface.yaml` is presentation only",
        "The compiler is the only interpreter",
        "Focus lives in the container's own status",
        "execution progress comes from execution records",
        "branch on a pack name",
    ):
        assert phrase in text, phrase


def test_the_architecture_note_maps_the_modules() -> None:
    text = _read("library/arch-operating-model.md")

    for phrase in (
        "model/compiler.py",
        "context/operating.py",
        "ProjectModel",
        "semantic_digest",
        "focus_missing",
    ):
        assert phrase in text, phrase
