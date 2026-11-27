"""The public docs teach agent-signed approval, not the removed human-only rule.

An approval record names its signer, and a named lead may sign when the charter
allows it. A doc that still calls `approve` human-only sends an agent to a
refusal the engine no longer raises, or teaches it to wait for a human where it
could truthfully sign on the human's behalf.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _public_docs() -> list[Path]:
    paths = [_ROOT / "README.md", _ROOT / "docs" / "how-it-works.md"]
    paths += sorted((_ROOT / "skills").rglob("*.md"))
    paths += sorted((_ROOT / "docs" / "agent-onboarding").rglob("*.md"))
    paths += sorted((_ROOT / "docs").glob("*.html"))
    return paths


#: Phrasings that teach the removed human-only approval rule.
_STALE = (
    "Human-only tools (`approve`",
    "Human-only actions - approving",
    "A human approves the charter",
    "human approves the boundary",
    "approve as an agent",
    "Never call `approve`",
    "a human must reapprove",
    "until a human reapproves",
    "This covers `approve`",
    "and `approve` yourself",
    "Human-only: approve",
    "reapprove as the human",
    "since a human approved it",
    "after the human approved it",
    "The human gate has not been passed",
)


def test_no_public_doc_calls_approve_human_only() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _public_docs():
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in _STALE:
            if phrase in text:
                offenders.append((path.relative_to(_ROOT).as_posix(), phrase))
    assert offenders == [], offenders


def test_the_key_docs_teach_the_signer_policy() -> None:
    for rel in (
        "docs/how-it-works.md",
        "docs/agent-onboarding/AGENTS.template.md",
        "docs/agent-onboarding/references/commands.md",
        "docs/agent-onboarding/references/refusals.md",
        "skills/context-rail-onboarding/SKILL.md",
        "skills/context-rail/references/commands.md",
        "skills/context-rail-ops/SKILL.md",
    ):
        assert "approval.agents" in _read(rel), rel


def test_the_shipped_coordination_skill_teaches_signing() -> None:
    text = _read("src/context_rail/skills/rail-coordinate/SKILL.md")
    assert "approval.agents" in text
    assert "--confirmer" in text
    assert "ops upgrade" in text


def test_the_html_guides_teach_the_signer_policy() -> None:
    for name in ("context-rail-v3-guide.html", "context-rail-v3-engine-graph.html"):
        text = _read(f"docs/{name}")
        assert "approval.agents" in text or "signer" in text, name