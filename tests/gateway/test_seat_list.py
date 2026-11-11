"""`seat list`: the live seats a store has granted, from the seat records."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.seats import SEAT_REVOKED
from helpers import run_cli, write_record


def listed(store: Path, **args: object) -> dict:
    result = invoke("seat list", dict(args), store=store)
    assert is_ok(result), result
    return result["value"]


def revoke(store: Path, record_id: str, seat: str, at: str) -> None:
    data = {"schema": 1, "seat": seat, "revoked_by": "human"}
    write_record(store, record_id, SEAT_REVOKED, data, at=at, actor="human")


def test_seat_list_shows_the_live_seats(role_store: SimpleNamespace) -> None:
    value = listed(role_store.store)

    assert value["role_mode"] is True
    assert [row["seat"] for row in value["seats"]] == [
        role_store.lead,
        role_store.orders_seat,
        role_store.billing_seat,
    ]
    first = value["seats"][0]
    assert first == {
        "seat": role_store.lead,
        "role": "lead",
        "anchor": "project",
        "profile": None,
        "granted_by": "human",
        "live": True,
    }
    assert value["seats"][1]["granted_by"] == role_store.lead
    assert value["seats"][1]["anchor"] == role_store.orders


def test_seat_list_shows_live_and_revoked(role_store: SimpleNamespace) -> None:
    revoke(
        role_store.store, "rev-orders", role_store.orders_seat, "2026-11-10T11:00:00Z"
    )

    live = listed(role_store.store)
    everything = listed(role_store.store, all=True)

    assert [row["seat"] for row in live["seats"]] == [
        role_store.lead,
        role_store.billing_seat,
    ]
    assert {row["seat"]: row["live"] for row in everything["seats"]} == {
        role_store.lead: True,
        role_store.orders_seat: False,
        role_store.billing_seat: True,
    }


def test_revoking_a_granter_ends_the_seats_it_granted(
    role_store: SimpleNamespace,
) -> None:
    revoke(role_store.store, "rev-lead", role_store.lead, "2026-11-10T11:00:00Z")

    assert listed(role_store.store)["seats"] == []
    assert all(not row["live"] for row in listed(role_store.store, all=True)["seats"])


def test_a_seat_granted_again_after_a_revoke_is_live_once(
    role_store: SimpleNamespace,
) -> None:
    from context_rail.model.seats import SEAT_GRANTED

    revoke(
        role_store.store, "rev-orders", role_store.orders_seat, "2026-11-10T11:00:00Z"
    )
    data = {
        "schema": 1,
        "seat": role_store.orders_seat,
        "role": "sprint-lead",
        "anchor": role_store.orders,
        "granted_by": role_store.lead,
    }
    write_record(
        role_store.store,
        "seat-again",
        SEAT_GRANTED,
        data,
        at="2026-11-10T12:00:00Z",
        actor=role_store.lead,
    )

    rows = listed(role_store.store, all=True)["seats"]

    mine = [row for row in rows if row["seat"] == role_store.orders_seat]
    assert [row["live"] for row in mine] == [False, True]


def test_seat_list_in_a_legacy_store_is_empty(approved_store: Path) -> None:
    assert listed(approved_store) == {"seats": [], "role_mode": False}


def test_seat_list_runs_from_the_command_line(role_store: SimpleNamespace) -> None:
    proc = run_cli(role_store.store.parent, "seat", "list", store=role_store.store)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert [row["seat"] for row in payload["value"]["seats"]][0] == role_store.lead
