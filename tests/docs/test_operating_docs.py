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
