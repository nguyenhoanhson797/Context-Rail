from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_all_code_graph_providers() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for value in ("builtin", "graphify", "gitnexus", "none"):
        assert value in text
    assert "uv tool install" in text
    assert "graphifyy" in text
    assert "npm install -g gitnexus" in text
    assert "rail ops codegraph setup" in text
    assert "RAIL_GRAPHIFY_BIN" in text
    assert "RAIL_GITNEXUS_BIN" in text


def test_how_it_works_documents_the_gitnexus_mirror() -> None:
    text = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")

    assert "GITNEXUS_HOME" in text
    assert "mirror" in text
    assert "PolyForm Noncommercial" in text
    assert "falls back to `builtin`" in text
    assert "readiness" in text


def test_the_ops_skill_names_the_external_providers() -> None:
    text = (ROOT / "skills" / "context-rail-v3-ops" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "ops codegraph setup" in text
    assert "graphify" in text
    assert "gitnexus" in text
    assert "GITNEXUS_HOME" in text
