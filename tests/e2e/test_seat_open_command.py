"""``rail seat open <seat> --print-env`` hands a tool the session it puts in RAIL_SESSION."""

from __future__ import annotations

import json
from types import SimpleNamespace

from context_rail.cli import _join_dash_values
from helpers import open_test_session, run_cli


def test_print_env_returns_a_token_that_acts_as_the_seat(
    role_store: SimpleNamespace,
) -> None:
    lead = open_test_session(role_store.store, role_store.lead)

    opened = run_cli(
        role_store.store.parent,
        "seat",
        "open",
        role_store.orders_seat,
        "--print-env",
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert opened.returncode == 0, opened.stderr or opened.stdout
    token = json.loads(opened.stdout)["value"]["token"]
    acting = run_cli(
        role_store.store.parent,
        "seat",
        "show",
        role_store.orders_seat,
        store=role_store.store,
        env={"RAIL_SESSION": token},
    )
    assert acting.returncode == 0, acting.stderr or acting.stdout


def test_the_token_is_not_printed_without_print_env(role_store: SimpleNamespace) -> None:
    lead = open_test_session(role_store.store, role_store.lead)

    proc = run_cli(
        role_store.store.parent,
        "seat",
        "open",
        role_store.orders_seat,
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "token" not in json.loads(proc.stdout)["value"]


def test_double_dash_after_other_commands_is_still_joined_as_before() -> None:
    assert _join_dash_values(["show", "t0", "--", "-x"]) == [
        "show",
        "t0",
        "--",
        "-x",
    ]