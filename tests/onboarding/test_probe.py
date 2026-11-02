"""The onboarding probe reads the project without writing anything."""

from __future__ import annotations

from pathlib import Path

from context_rail.gateway.handlers.onboarding_pipeline import probe
from helpers import init_store, run_git


def test_probe_an_empty_project(tmp_path: Path) -> None:
    found = probe(tmp_path, tmp_path / "context-rail")

    assert found["store"]["exists"] is False
    assert found["store"]["pack"] == "basic"
    assert found["store"]["error"] is None
    assert found["picture"] == {"exists": False, "slugs": []}
    assert found["approval"]["state"] == "missing"
    assert found["workspace"]["exists"] is False
    assert found["agents"]["exists"] is False
    assert found["git"]["exists"] is False


def test_probe_a_scaffold_has_no_picture_or_workspace(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    found = probe(tmp_path, store)

    assert found["store"]["exists"] is True
    assert found["store"]["pack"] == "basic"
    assert found["picture"] == {"exists": False, "slugs": []}
    assert found["approval"]["state"] == "missing"
    assert found["workspace"]["exists"] is False


def test_probe_a_picture_and_its_slugs(unapproved_store: Path) -> None:
    found = probe(unapproved_store.parent, unapproved_store)

    assert found["picture"]["exists"] is True
    assert found["picture"]["slugs"] == [
        "ship-invoicing",
        "invoices-on-autopilot",
        "send-invoice",
        "track-payment",
        "chase-overdue",
        "wont",
    ]
    assert found["approval"]["state"] == "missing"
    assert found["workspace"]["exists"] is True


def test_probe_sees_a_recorded_approval(approved_store: Path) -> None:
    found = probe(approved_store.parent, approved_store)

    assert found["picture"]["exists"] is True
    assert found["approval"]["state"] == "recorded"


def test_probe_reports_agent_files_and_git(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("fixture\n", encoding="utf-8")
    run_git(tmp_path, "init")

    found = probe(tmp_path, tmp_path / "context-rail")

    assert found["agents"]["exists"] is True
    assert found["git"]["exists"] is True


def test_probe_reports_a_broken_manifest_without_raising(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    (store / "rail.yaml").write_bytes(b"schema: [")

    found = probe(tmp_path, store)

    assert found["store"]["exists"] is True
    assert found["store"]["error"]
    assert found["store"]["pack"] is None
    assert found["picture"]["exists"] is False
