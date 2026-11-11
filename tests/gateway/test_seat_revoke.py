"""Revoking seats: the human and a granting ancestor, never a sibling or self."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def revoke(store: Path, seat: str, *, as_seat: str | None = None) -> dict[str, Any]:
    if as_seat is None:
        with as_human():
            return invoke("seat revoke", {"seat": seat}, store=store, actor=None)
    token = open_test_session(store, as_seat)
    with caller_identity(None, None, session=token):
        return invoke("seat revoke", {"seat": seat}, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def live_seats(store: Path) -> set[str]:
    with as_human():
        listed = invoke("seat list", {}, store=store, actor=None)
    assert is_ok(listed), listed
    return {row["seat"] for row in listed["value"]["seats"]}


def test_revoke_by_the_human_and_by_a_granting_ancestor(
    role_store: SimpleNamespace,
) -> None:
    assert is_ok(revoke(role_store.store, role_store.orders_seat, as_seat=role_store.lead))

    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "lead-2", "role": "lead", "anchor": "project"},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(granted), granted
    assert is_ok(revoke(role_store.store, "lead-2"))

    assert live_seats(role_store.store) == {role_store.lead, role_store.billing_seat}


def test_a_sibling_cannot_revoke(role_store: SimpleNamespace) -> None:
    result = revoke(role_store.store, role_store.billing_seat, as_seat=role_store.orders_seat)

    assert refusal(result) == ("PERMISSION_DENIED", "revoke_denied")


def test_a_seat_cannot_revoke_itself(role_store: SimpleNamespace) -> None:
    result = revoke(role_store.store, role_store.orders_seat, as_seat=role_store.orders_seat)

    assert refusal(result) == ("PERMISSION_DENIED", "revoke_denied")


def test_revoke_cascades_in_seat_list(role_store: SimpleNamespace) -> None:
    assert is_ok(revoke(role_store.store, role_store.lead))

    assert live_seats(role_store.store) == set()


def test_revoking_an_unknown_seat_is_refused(role_store: SimpleNamespace) -> None:
    result = revoke(role_store.store, "mgr-ghost")

    assert refusal(result) == ("NOT_FOUND", "seat_unknown")


def test_a_revoked_seats_token_is_refused_next_call(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)
    assert is_ok(revoke(role_store.store, role_store.orders_seat))

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")
