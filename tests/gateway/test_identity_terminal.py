"""Terminal human proof and the read-only seat lens."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import (
    HAS_TTY,
    LENS_SEAT,
    caller_identity,
)
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.identity import IdentityRefused, resolve_identity
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import open_test_session


def context_for(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store, manifest=manifest, pack=load_pack(manifest.pack, store), actor="human"
    )


def refusal(result: dict) -> tuple[str, str]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], error["details"]["reason"]


def test_tty_without_session_is_human(role_store: SimpleNamespace) -> None:
    identity = resolve_identity(
        context_for(role_store.store), actor=None, session=None, tty=True
    )

    assert (identity.kind, identity.name, identity.human, identity.lead) == (
        "human",
        "human",
        True,
        True,
    )


def test_tty_with_a_session_is_never_human(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)

    identity = resolve_identity(
        context_for(role_store.store), actor=None, session=token, tty=True
    )

    assert identity.kind == "seat"
    assert identity.human is False
    assert identity.lead is False


def test_tty_with_rail_run_set_is_not_human(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_RUN", "run-1")

    identity = resolve_identity(
        context_for(role_store.store), actor=None, session=None, tty=True
    )

    assert identity.kind == "anonymous"


def test_no_tty_no_session_is_anonymous(role_store: SimpleNamespace) -> None:
    identity = resolve_identity(
        context_for(role_store.store), actor=None, session=None, tty=False
    )

    assert identity.kind == "anonymous"
    assert identity.name == "anonymous"


def test_human_channel_makes_a_human(role_store: SimpleNamespace) -> None:
    identity = resolve_identity(
        context_for(role_store.store),
        actor=None,
        session=None,
        tty=False,
        human_channel=True,
    )

    assert (identity.kind, identity.name, identity.human) == ("human", "human", True)


def test_lens_on_own_seat_is_normal(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)

    identity = resolve_identity(
        context_for(role_store.store),
        actor=None,
        session=token,
        lens=role_store.orders_seat,
    )

    assert identity.kind == "seat"
    assert identity.view_as is None


def test_lens_by_a_lead_on_a_seat_inside_its_read_reach_is_view_as(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(role_store.store, role_store.lead)
    context = context_for(role_store.store)

    identity = resolve_identity(
        context, actor=None, session=token, lens=role_store.billing_seat
    )

    assert identity.kind == "seat"
    assert identity.name == role_store.lead
    assert identity.view_as is not None
    assert identity.view_as.seat == role_store.billing_seat

    context.identity = identity
    assert context.reach is not None
    assert context.reach.can_read(role_store.b1)


def test_lens_outside_reach_is_refused(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)

    with pytest.raises(IdentityRefused) as raised:
        resolve_identity(
            context_for(role_store.store),
            actor=None,
            session=token,
            lens=role_store.billing_seat,
        )

    assert raised.value.code == "PERMISSION_DENIED"
    assert raised.value.details["reason"] == "lens_out_of_reach"

    with caller_identity(None, None, session=token, lens=role_store.billing_seat):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "lens_out_of_reach")


def test_terminal_lens_on_any_seat_is_view_as(role_store: SimpleNamespace) -> None:
    identity = resolve_identity(
        context_for(role_store.store),
        actor=None,
        session=None,
        tty=True,
        lens=role_store.billing_seat,
    )

    assert identity.kind == "human"
    assert identity.human is True
    assert identity.view_as is not None
    assert identity.view_as.seat == role_store.billing_seat


def test_lens_on_an_unknown_seat_lists_candidates(role_store: SimpleNamespace) -> None:
    with pytest.raises(IdentityRefused) as raised:
        resolve_identity(
            context_for(role_store.store),
            actor=None,
            session=None,
            tty=True,
            lens="mgr-ghost",
        )

    assert raised.value.code == "PERMISSION_DENIED"
    assert raised.value.details["reason"] == "lens_unknown_seat"
    assert raised.value.details["candidates"] == sorted(
        [role_store.lead, role_store.orders_seat, role_store.billing_seat]
    )


def test_caller_identity_holds_the_terminal_flag_and_lens() -> None:
    with caller_identity(None, None, tty=True, lens="mgr-orders"):
        assert HAS_TTY.get() is True
        assert LENS_SEAT.get() == "mgr-orders"

    assert HAS_TTY.get() is False
    assert LENS_SEAT.get() is None


def test_a_terminal_call_is_recorded_as_the_human(role_store: SimpleNamespace) -> None:
    with caller_identity(None, None, tty=True):
        added = invoke(
            "add",
            {"preset": "story", "title": "O3", "rel": [f"part_of={role_store.orders}"]},
            store=role_store.store,
        )

    assert is_ok(added), added
    actors = {
        json.loads(path.read_text(encoding="utf-8")).get("actor")
        for path in (role_store.store / "records").rglob("*.json")
        if added["value"]["slug"] in path.read_text(encoding="utf-8")
    }
    assert "human" in actors
