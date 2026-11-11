"""``rail seat open <seat> -- <command>`` launches with the session in its env."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from context_rail.cli import _join_dash_values
from helpers import open_test_session, run_cli


def test_the_launched_command_sees_rail_session(role_store: SimpleNamespace) -> None:
    lead = open_test_session(role_store.store, role_store.lead)
    command = [
        sys.executable,
        "-c",
        "import os;print(bool(os.environ.get('RAIL_SESSION')))",
    ]

    proc = run_cli(
        role_store.store.parent,
        "seat",
        "open",
        role_store.orders_seat,
        "--",
        *command,
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True"


def test_the_token_is_not_printed_even_with_print_env(
    role_store: SimpleNamespace,
) -> None:
    lead = open_test_session(role_store.store, role_store.lead)
    command = [sys.executable, "-c", "print('ran')"]

    proc = run_cli(
        role_store.store.parent,
        "seat",
        "open",
        role_store.orders_seat,
        "--print-env",
        "--",
        *command,
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ran"


def test_double_dash_after_other_commands_is_still_joined_as_before() -> None:
    assert _join_dash_values(["show", "t0", "--", "-x"]) == [
        "show",
        "t0",
        "--",
        "-x",
    ]
