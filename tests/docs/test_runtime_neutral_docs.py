"""The public docs describe a rail that records work, not one that launches agents."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
FORBIDDEN = (
    "loop dispatch",
    "loop tick",
    "loop merge",
    "cto tick",
    "droid exec",
    "RAIL_DROID_BIN",
    "rail-report",
    "RAIL_RUNNER",
    "doctor --repair",
    "doctor --fix",
)


def _public_docs() -> list[Path]:
    paths = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "docs" / "how-it-works.md"]
    paths += sorted((ROOT / "docs" / "agent-onboarding").rglob("*.md"))
    paths += sorted((ROOT / "skills").rglob("*.md"))
    return [path for path in paths if path.is_file()]


def test_public_docs_name_no_launcher() -> None:
    docs = _public_docs()
    assert len(docs) > 10

    for path in docs:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            assert phrase not in text, (path.relative_to(ROOT).as_posix(), phrase)


def test_harnesses_doc_allows_native_subagents() -> None:
    text = (ROOT / "docs/agent-onboarding/references/harnesses.md").read_text(encoding="utf-8")

    assert "Native sub-agents" in text
    assert "Droid" in text
    assert "Codex" in text and "opencode" in text.lower()


def test_loop_skill_teaches_the_record_loop() -> None:
    text = (ROOT / "skills/context-rail-v3-loop/SKILL.md").read_text(encoding="utf-8")

    for phrase in ("rail loop brief", "rail loop candidate", "rail loop review", "rail loop report"):
        assert phrase in text, phrase


def test_agents_md_states_the_runtime_neutral_rule() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "never launches an agent" in text
    assert "2026-11-12-runtime-neutral-engine-design.md" in text


def test_pack_authoring_skill_uses_the_queue_flag() -> None:
    for rel in (
        "skills/context-rail-v3-pack-authoring/SKILL.md",
        "skills/context-rail-v3-workflows/SKILL.md",
        "docs/agent-onboarding/references/capabilities.md",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "dispatch: false" not in text, rel
        assert "queue: false" in text, rel