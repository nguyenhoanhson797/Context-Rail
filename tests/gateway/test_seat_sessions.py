"""Opening and closing seat sessions, and the permissions on both."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.store.sessions import resolve_session
from helpers import as_human, open_test_session


def edit_roles(store: Path, change: Any) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document["operating"]["roles"])
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )


def add_human_role(store: Path) -> None:
    def change(roles: dict[str, Any]) -> None:
        roles["human-lead"] = {
            "anchor": "project",
            "reach": {"read": ["project"], "write": ["project"]},
            "can": {"grant": []},
            "human": True,
            "workspace": "none",
        }
        roles["lead"]["can"]["grant"].append("human-lead")

    edit_roles(store, change)


def open_seat(
    store: Path,
    seat: str,
    *,
    ttl_hours: int | None = None,
    print_env: bool = False,
    as_seat: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {"seat": seat, "print_env": print_env}
    if ttl_hours is not None:
        args["ttl_hours"] = ttl_hours
    if as_seat is None:
        with as_human():
            return invoke("seat open", args, store=store, actor=None)
    token = open_test_session(store, as_seat)
    with caller_identity(None, None, session=token):
        return invoke("seat open", args, store=store)


def close_seat(
    store: Path, seat: str, *, all_sessions: bool = False, as_seat: str | None = None
) -> dict[str, Any]:
    args: dict[str, Any] = {"all": True} if all_sessions else {"seat": seat}
    if as_seat is None:
        with as_human():
            return invoke("seat close", args, store=store, actor=None)
    token = open_test_session(store, as_seat)
    with caller_identity(None, None, session=token):
        return invoke("seat close", args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def test_the_human_opens_a_live_seat_session(role_store: SimpleNamespace) -> None:
    opened = open_seat(role_store.store, role_store.orders_seat, print_env=True)

    assert is_ok(opened), opened
    assert opened["value"]["seat"] == role_store.orders_seat
    assert opened["value"]["expires"]
    session = resolve_session(role_store.store, opened["value"]["token"])
    assert session is not None and session.seat == role_store.orders_seat


def test_a_seat_opens_its_subordinate_but_not_itself_or_a_sibling(
    role_store: SimpleNamespace,
) -> None:
    subordinate = open_seat(role_store.store, role_store.orders_seat, as_seat=role_store.lead)
    itself = open_seat(role_store.store, role_store.lead, as_seat=role_store.lead)
    sibling = open_seat(
        role_store.store, role_store.billing_seat, as_seat=role_store.orders_seat
    )

    assert is_ok(subordinate), subordinate
    assert refusal(itself) == ("PERMISSION_DENIED", "open_denied")
    assert refusal(sibling) == ("PERMISSION_DENIED", "open_denied")


def test_a_human_role_seat_can_only_be_opened_by_the_human(
    role_store: SimpleNamespace,
) -> None:
    add_human_role(role_store.store)
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "human-2", "role": "human-lead", "anchor": "project"},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(granted), granted

    by_seat = open_seat(role_store.store, "human-2", as_seat=role_store.lead)
    by_human = open_seat(role_store.store, "human-2")

    assert refusal(by_seat) == ("PERMISSION_DENIED", "human_role_requires_human")
    assert is_ok(by_human), by_human


def test_ttl_hours_above_168_is_refused(role_store: SimpleNamespace) -> None:
    opened = open_seat(role_store.store, role_store.orders_seat, ttl_hours=169)

    assert refusal(opened) == ("VALIDATION_FAILED", "ttl_too_long")


def test_print_env_shows_the_token_and_the_default_call_does_not(
    role_store: SimpleNamespace,
) -> None:
    plain = open_seat(role_store.store, role_store.orders_seat)
    shown = open_seat(role_store.store, role_store.orders_seat, print_env=True)

    assert is_ok(plain), plain
    assert "token" not in plain["value"]
    assert is_ok(shown), shown
    assert resolve_session(role_store.store, shown["value"]["token"]) is not None


def test_close_ends_the_session(role_store: SimpleNamespace) -> None:
    opened = open_seat(role_store.store, role_store.orders_seat, print_env=True)
    assert is_ok(opened), opened
    token = opened["value"]["token"]

    closed = close_seat(role_store.store, role_store.orders_seat)
    assert is_ok(closed), closed
    assert closed["value"]["closed"] >= 1

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)
    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")


def test_close_all_ends_every_session(role_store: SimpleNamespace) -> None:
    orders = open_seat(role_store.store, role_store.orders_seat, print_env=True)
    billing = open_seat(role_store.store, role_store.billing_seat, print_env=True)
    assert is_ok(orders) and is_ok(billing)

    closed = close_seat(role_store.store, "", all_sessions=True)

    assert is_ok(closed), closed
    for opened in (orders, billing):
        with caller_identity(None, None, session=opened["value"]["token"]):
            result = invoke("seat list", {}, store=role_store.store)
        assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")
