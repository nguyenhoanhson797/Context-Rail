"""The bare init pipeline reconciles and resumes an onboarding."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.gateway.handlers.onboarding_pipeline import render_card


def test_bare_init_scaffolds_and_seeds_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    result = invoke("init", {"dir": str(tmp_path)}, actor="human")

    assert is_ok(result), result
    value = result["value"]
    stages = value["stages"]
    assert value["dry_run"] is False
    assert stages["skeleton"]["state"] == "written"
    assert stages["workspace"]["state"] == "written"
    assert stages["picture"]["state"] == "skipped"
    assert stages["picture"]["reason"] == "no_picture_source"
    assert stages["approval"]["reason"] == "picture_deferred"
    assert value["next"][0] == "rail init --interview"
    store = Path(value["store"])
    assert (store / "workspace" / "cto" / "profile.md").is_file()
    assert not (store / "charter.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_bare_init_resumes_an_existing_picture(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    result = invoke("init", {"dir": str(unapproved_store.parent)}, actor="human")

    assert is_ok(result), result
    value = result["value"]
    stages = value["stages"]
    assert stages["skeleton"]["state"] == "unchanged"
    assert stages["workspace"]["state"] == "unchanged"
    assert stages["picture"]["state"] == "skipped"
    assert stages["picture"]["reason"] == "picture_present"
    assert stages["approval"]["reason"] == "no_terminal"
    assert stages["agents"]["state"] == "written"
    assert (unapproved_store.parent / "AGENTS.md").is_file()


def test_bare_init_resumes_the_store_pack_without_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = invoke(
        "init",
        {"dir": str(tmp_path), "pack": "flow", "scaffold": True},
        actor="human",
    )
    assert is_ok(created), created
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    result = invoke("init", {"dir": str(tmp_path)}, actor="human")

    assert is_ok(result), result
    assert result["value"]["pack"] == "flow"
    assert result["value"]["stages"]["skeleton"]["state"] == "unchanged"


def test_bare_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = invoke(
        "init", {"dir": str(tmp_path), "dry_run": True}, actor="human"
    )

    assert is_ok(result), result
    value = result["value"]
    assert value["dry_run"] is True
    assert value["stages"]["skeleton"]["state"] == "planned"
    assert value["next"][0] == "rail init --interview"
    assert not (tmp_path / "context-rail").exists()


def test_bare_init_can_approve_on_a_terminal(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = ["Ada", "APPROVE charter"]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": replies.pop(0))

    result = invoke("init", {"dir": str(unapproved_store.parent)}, actor="human")

    assert is_ok(result), result
    assert result["value"]["approval"] == "approved"
    assert result["value"]["stages"]["approval"]["state"] == "approved"
    shown = invoke("show", {"slug": "charter"}, store=unapproved_store, actor="human")
    assert shown["value"]["approval"]["state"] == "approved"


def test_scaffold_keeps_the_old_result_and_refusal(tmp_path: Path) -> None:
    first = invoke("init", {"dir": str(tmp_path), "scaffold": True}, actor="human")
    assert is_ok(first), first
    assert set(first["value"]) == {"store", "pack"}
    store = tmp_path / "context-rail"
    assert not (store / "workspace" / "cto" / "profile.md").exists()

    second = invoke("init", {"dir": str(tmp_path), "scaffold": True}, actor="human")
    assert second["error"]["code"] == "CONFLICT"


def test_scaffold_rejects_a_picture_mode(tmp_path: Path) -> None:
    result = invoke(
        "init",
        {"dir": str(tmp_path), "scaffold": True, "interview": True},
        actor="human",
    )

    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_scaffold_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = invoke(
        "init",
        {"dir": str(tmp_path), "scaffold": True, "dry_run": True},
        actor="human",
    )

    assert is_ok(result), result
    assert result["value"]["dry_run"] is True
    assert result["value"]["stages"] == {"skeleton": {"state": "planned"}}
    assert not (tmp_path / "context-rail").exists()


def test_render_card_names_every_stage(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    result = invoke("init", {"dir": str(unapproved_store.parent)}, actor="human")

    card = render_card(result["value"])

    assert "context rail - onboarding" in card
    assert "approval" in card
    assert "rail --actor human approve charter" in card
    assert "next:" in card
