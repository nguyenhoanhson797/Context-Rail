"""Human-only tools need an explicitly named human."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import run_cli


def _approve_args(store: Path) -> dict[str, str]:
    shown = invoke("show", {"slug": "charter"}, store=store)
    assert is_ok(shown), shown
    return {
        "target": "charter",
        "confirm": "APPROVE charter",
        "confirmer": "Ada",
        "expect": shown["value"]["approval"]["snapshot_digest"],
    }


def test_human_tools_refuse_an_unnamed_actor(
    unapproved_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAIL_ACTOR", raising=False)
    calls = (
        ("approve", _approve_args(unapproved_store), unapproved_store),
        ("pack use", {"name": "flow", "expect": "0" * 16}, unapproved_store),
        (
            "migrate",
            {"source": str(tmp_path / "legacy"), "dir": str(tmp_path / "migrated")},
            None,
        ),
    )

    for name, args, store in calls:
        result = invoke(name, args, store=store, actor=None)
        assert result["ok"] is False, (name, result)
        assert result["error"]["code"] == "PERMISSION_DENIED", (name, result)
        assert "explicitly named human" in result["error"]["message"], (name, result)


def test_an_explicit_human_may_approve(unapproved_store: Path) -> None:
    result = invoke(
        "approve", _approve_args(unapproved_store), store=unapproved_store, actor="human"
    )

    assert is_ok(result), result


def test_rail_actor_human_may_approve(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")

    result = invoke("approve", _approve_args(unapproved_store), store=unapproved_store)

    assert is_ok(result), result


def test_ordinary_writes_keep_the_implicit_default(
    unapproved_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAIL_ACTOR", raising=False)

    result = invoke(
        "add", {"preset": "task", "title": "Implicit actor"}, store=unapproved_store
    )

    assert is_ok(result), result


def test_cli_human_tool_needs_an_explicit_actor(
    unapproved_store: Path, tmp_path: Path
) -> None:
    args = _approve_args(unapproved_store)
    command = (
        "approve",
        "charter",
        "--confirm",
        args["confirm"],
        "--confirmer",
        args["confirmer"],
        "--expect",
        args["expect"],
    )

    refused = run_cli(tmp_path, *command, store=unapproved_store)
    assert refused.returncode == 1, refused.stdout
    payload = json.loads(refused.stdout)
    assert payload["error"]["code"] == "PERMISSION_DENIED"

    accepted = run_cli(
        tmp_path, "--actor", "human", *command, store=unapproved_store
    )
    assert accepted.returncode == 0, accepted.stdout
    assert json.loads(accepted.stdout)["ok"] is True
