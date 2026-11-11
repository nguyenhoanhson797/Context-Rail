"""The ``rail <seat>`` lens and the legacy no-roles answers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from context_rail.cli import _split_seat_lens
from helpers import open_test_session, run_cli


def test_split_seat_lens() -> None:
    head, lens = _split_seat_lens(["--store", "x", "mgr-orders", "next"])
    assert lens == "mgr-orders"
    assert head == ["--store", "x", "next"]

    head, lens = _split_seat_lens(["mgr-orders"])
    assert lens is None
    assert head == ["seat", "show", "mgr-orders"]

    assert _split_seat_lens(["next"]) == (["next"], None)
    assert _split_seat_lens(["status"]) == (["status"], None)
    assert _split_seat_lens(["seat", "list"]) == (["seat", "list"], None)


def test_rail_seat_next_returns_only_that_seats_queue(
    role_store: SimpleNamespace,
) -> None:
    lead = open_test_session(role_store.store, role_store.lead)

    proc = run_cli(
        role_store.store.parent,
        "mgr-orders",
        "next",
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    value = json.loads(proc.stdout)["value"]
    assert set(value["queue"]) == {role_store.o1, role_store.o2}


def test_plain_next_with_a_session_means_its_own_seat(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)

    proc = run_cli(
        role_store.store.parent,
        "next",
        store=role_store.store,
        env={"RAIL_SESSION": token},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    value = json.loads(proc.stdout)["value"]
    assert set(value["queue"]) == {role_store.o1, role_store.o2}


def test_rail_seat_write_without_its_session_is_refused(
    role_store: SimpleNamespace,
) -> None:
    lead = open_test_session(role_store.store, role_store.lead)

    proc = run_cli(
        role_store.store.parent,
        "mgr-orders",
        "add",
        "story",
        "Lens write",
        "--rel",
        f"part_of={role_store.orders}",
        store=role_store.store,
        env={"RAIL_SESSION": lead},
    )

    assert proc.returncode == 1
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "lens_read_only"


def test_unknown_head_in_a_legacy_store_is_refused_with_the_no_roles_hint(
    approved_store: Path,
) -> None:
    proc = run_cli(approved_store.parent, "mgr-orders", store=approved_store)

    assert proc.returncode == 1
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert "declares no roles" in error["message"]


def test_seat_commands_in_a_legacy_store_say_no_roles(approved_store: Path) -> None:
    proc = run_cli(approved_store.parent, "seat", "list", store=approved_store)

    assert proc.returncode == 1
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert "declares no roles" in error["message"]
