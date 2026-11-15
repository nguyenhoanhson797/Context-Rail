"""Tests for the global ``--session-env NAME`` option across CLI, daemon, and MCP."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from context_rail.cli import _command_index, _expand_legacy, _join_dash_values, _split_seat_lens
from helpers import actors_for, open_test_session, run_cli, serving_daemon


def _json(proc) -> dict:
    return json.loads(proc.stdout)


def test_invalid_name_is_refused(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        role_store.store.parent,
        "--session-env",
        "bad-name",
        "seat",
        "show",
        store=role_store.store,
        env={"RAIL_SESSION": token},
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    error = _json(proc)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["reason"] == "session_env_invalid"
    assert error["details"]["env"] == "bad-name"
    assert token not in proc.stdout + proc.stderr


def test_unset_variable_is_refused(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        role_store.store.parent,
        "--session-env",
        "RAIL_SESSION_MISSING",
        "seat",
        "show",
        store=role_store.store,
        env={"RAIL_SESSION": token, "RAIL_SESSION_MISSING": None},
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    error = _json(proc)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["reason"] == "session_env_unset"
    assert error["details"]["env"] == "RAIL_SESSION_MISSING"
    assert token not in proc.stdout + proc.stderr


def test_empty_variable_is_refused(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        role_store.store.parent,
        "--session-env",
        "RAIL_SESSION_EMPTY",
        "seat",
        "show",
        store=role_store.store,
        env={"RAIL_SESSION": token, "RAIL_SESSION_EMPTY": "   "},
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    error = _json(proc)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["reason"] == "session_env_unset"
    assert error["details"]["env"] == "RAIL_SESSION_EMPTY"
    assert token not in proc.stdout + proc.stderr


def test_named_variable_carries_the_seat(role_store: SimpleNamespace) -> None:
    lead_token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        role_store.store.parent,
        "--session-env",
        "RAIL_SESSION_X",
        "seat",
        "show",
        store=role_store.store,
        env={"RAIL_SESSION": "bogus-token-value", "RAIL_SESSION_X": lead_token},
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = _json(proc)
    assert payload["ok"] is True
    assert payload["value"]["seat"] == role_store.lead
    assert lead_token not in proc.stdout + proc.stderr


def test_equals_form_parses_through_command_index_and_cli(
    role_store: SimpleNamespace,
) -> None:
    assert _command_index(["--session-env=RAIL_SESSION_X", "status"]) == 1
    assert _command_index(["--store=s", "--session-env=RAIL_SESSION_X", "status"]) == 2
    assert _expand_legacy(["--session-env=RAIL_SESSION_X", "status"]) == [
        "--session-env=RAIL_SESSION_X",
        "read",
        "status",
    ]
    assert _split_seat_lens(["--session-env=RAIL_SESSION_X", "mgr-orders", "next"]) == (
        ["--session-env=RAIL_SESSION_X", "next"],
        "mgr-orders",
    )
    assert _join_dash_values(["--session-env", "-bad", "status"]) == [
        "--session-env=-bad",
        "status",
    ]

    lead_token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        role_store.store.parent,
        "--session-env=RAIL_SESSION_X",
        "seat",
        "show",
        store=role_store.store,
        env={"RAIL_SESSION": "bogus", "RAIL_SESSION_X": lead_token},
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert _json(proc)["value"]["seat"] == role_store.lead
    assert lead_token not in proc.stdout + proc.stderr


def test_token_for_another_store_is_refused(
    role_store: SimpleNamespace, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    other_store = other / "context-rail"
    shutil.copytree(
        role_store.store, other_store, ignore=shutil.ignore_patterns(".rail-local")
    )

    lead_token = open_test_session(role_store.store, role_store.lead)
    proc = run_cli(
        other,
        "--session-env",
        "RAIL_SESSION_X",
        "seat",
        "list",
        store=other_store,
        env={"RAIL_SESSION_X": lead_token},
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    error = _json(proc)["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "session_invalid"
    assert lead_token not in proc.stdout + proc.stderr


def test_token_never_appears_in_output(role_store: SimpleNamespace) -> None:
    lead_token = open_test_session(role_store.store, role_store.lead)

    for extra_args, env in [
        (["--session-env", "RAIL_SESSION_X", "seat", "show"], {"RAIL_SESSION_X": lead_token}),
        (
            ["--session-env", "bad-name", "seat", "show"],
            {"RAIL_SESSION": lead_token},
        ),
        (
            ["--session-env", "RAIL_SESSION_MISSING", "seat", "show"],
            {"RAIL_SESSION": lead_token, "RAIL_SESSION_MISSING": None},
        ),
        (
            ["--session-env", "RAIL_SESSION_EMPTY", "seat", "show"],
            {"RAIL_SESSION": lead_token, "RAIL_SESSION_EMPTY": "  "},
        ),
    ]:
        proc = run_cli(
            role_store.store.parent,
            *extra_args,
            store=role_store.store,
            env=env,
        )
        assert lead_token not in proc.stdout
        assert lead_token not in proc.stderr


def test_option_works_through_the_daemon(
    role_store: SimpleNamespace, daemon_enabled: None
) -> None:
    lead_token = open_test_session(role_store.store, role_store.lead)
    with serving_daemon(role_store.store):
        proc = run_cli(
            role_store.store.parent,
            "--session-env",
            "RAIL_SESSION_X",
            "add",
            "story",
            "Daemon named env story",
            "--rel",
            f"part_of={role_store.orders}",
            store=role_store.store,
            env={"RAIL_SESSION": "bogus", "RAIL_SESSION_X": lead_token},
        )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = _json(proc)
    assert payload["ok"] is True
    assert role_store.lead in actors_for(role_store.store, payload["value"]["slug"])
    assert lead_token not in proc.stdout + proc.stderr


def test_actor_still_applies_without_roles(approved_store: Path) -> None:
    proc = run_cli(
        approved_store.parent,
        "--actor",
        "cto",
        "--session-env",
        "RAIL_SESSION_X",
        "add",
        "task",
        "Basic store actor task",
        store=approved_store,
        env={"RAIL_SESSION_X": "some-master-token"},
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = _json(proc)
    assert payload["ok"] is True
    assert "cto" in actors_for(approved_store, payload["value"]["slug"])
    assert "some-master-token" not in proc.stdout + proc.stderr


def test_mcp_serve_pins_the_named_session(role_store: SimpleNamespace) -> None:
    orders_token = open_test_session(role_store.store, role_store.orders_seat)
    repo_src = Path(__file__).resolve().parents[2] / "src"

    async def scenario() -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_src)
        env.pop("RAIL_ACTOR", None)
        env["RAIL_SESSION"] = "bogus-child-token"
        env["RAIL_SESSION_X"] = orders_token
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "context_rail",
                "--store",
                str(role_store.store),
                "--session-env",
                "RAIL_SESSION_X",
                "mcp",
                "serve",
            ],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                assert "add" in names
                assert "relocate" not in names
                added = await client.call_tool(
                    "add",
                    {
                        "preset": "story",
                        "title": "MCP named session env",
                        "rel": [f"part_of={role_store.orders}"],
                    },
                )
                assert added.is_error is False, added.structured_content
                slug = added.structured_content["value"]["slug"]
                assert role_store.orders_seat in actors_for(role_store.store, slug)

    anyio.run(scenario)
