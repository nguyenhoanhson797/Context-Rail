"""An unnamed caller is the human only on a person's channel; anywhere else it only reads."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity, human_channel
from context_rail.gateway.gateway import invoke
from helpers import refusal, run_cli


def add_note(leaf_item: SimpleNamespace, **kw: object) -> dict:
    return invoke(
        "add",
        {"preset": "task", "title": "Added without a name"},
        store=leaf_item.store,
        **kw,
    )


def test_unnamed_non_terminal_write_refused_actor_required(
    leaf_item: SimpleNamespace,
) -> None:
    result = add_note(leaf_item)

    assert refusal(result) == ("PERMISSION_DENIED", "actor_required")
    assert "--actor" in result["error"]["details"]["hint"]


def test_unnamed_non_terminal_init_refused_actor_required(tmp_path: Path) -> None:
    result = invoke("init", {"dir": str(tmp_path / "fresh")})

    assert refusal(result) == ("PERMISSION_DENIED", "actor_required")
    assert not (tmp_path / "fresh").exists()


def test_unnamed_non_terminal_read_allowed(leaf_item: SimpleNamespace) -> None:
    result = invoke("show", {"slug": leaf_item.slug}, store=leaf_item.store)

    assert is_ok(result), result


def test_unnamed_terminal_caller_is_human(leaf_item: SimpleNamespace) -> None:
    with caller_identity(None, None, tty=True):
        result = add_note(leaf_item)

    assert is_ok(result), result


def test_ui_human_channel_is_human(leaf_item: SimpleNamespace) -> None:
    with human_channel():
        result = add_note(leaf_item)

    assert is_ok(result), result


def test_actor_human_flag_works_without_terminal(leaf_item: SimpleNamespace) -> None:
    assert is_ok(add_note(leaf_item, actor="human"))


def test_a_named_agent_still_writes(leaf_item: SimpleNamespace) -> None:
    assert is_ok(add_note(leaf_item, actor="dev"))


def test_rail_run_env_is_ignored(
    leaf_item: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_RUN", "run-1")

    with caller_identity(None, None, tty=True):
        result = add_note(leaf_item)

    assert is_ok(result), result


def test_cli_unnamed_write_prints_actor_hint(leaf_item: SimpleNamespace) -> None:
    proc = run_cli(
        leaf_item.root,
        "change",
        "add",
        "task",
        "No name",
        store=leaf_item.store,
    )

    assert proc.returncode == 1, proc.stdout
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "actor_required"
    assert "--actor" in error["details"]["hint"]


def test_cli_unnamed_read_works(leaf_item: SimpleNamespace) -> None:
    proc = run_cli(leaf_item.root, "show", leaf_item.slug, store=leaf_item.store)

    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_seat_open_has_no_command_wrapper(role_store: SimpleNamespace) -> None:
    proc = run_cli(
        role_store.store.parent,
        "seat",
        "open",
        role_store.orders_seat,
        "--",
        "echo",
        "launched",
        store=role_store.store,
    )

    assert proc.returncode != 0
    assert "unrecognized arguments" in json.loads(proc.stdout)["error"]["message"]


def test_refusal_hints_name_print_env(role_store: SimpleNamespace) -> None:
    result = invoke("seat list", {}, store=role_store.store, actor="cto")

    assert refusal(result) == ("PERMISSION_DENIED", "actor_flag_refused")
    assert result["error"]["details"]["command"] == "rail seat open <seat> --print-env"