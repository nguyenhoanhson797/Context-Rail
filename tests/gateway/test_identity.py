"""Identity: who a call acts as, from a session in role mode and a name in legacy mode."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.identity import Identity, IdentityRefused, resolve_identity
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.seats import SEAT_GRANTED, SEAT_REVOKED
from helpers import as_human, open_test_session, write_record

UNKNOWN = "x" * 43


def context_for(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store, manifest=manifest, pack=load_pack(manifest.pack, store), actor="human"
    )


def refusal(result: dict) -> tuple[str, str]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], error["details"]["reason"]


def test_legacy_cto_and_human_are_leads(approved_store: Path) -> None:
    context = context_for(approved_store)

    cto = resolve_identity(context, actor="cto", session=None)
    human = resolve_identity(context, actor="human", session=None)

    assert (cto.kind, cto.name, cto.human, cto.lead) == ("legacy", "cto", False, True)
    assert (human.kind, human.name, human.human, human.lead) == ("legacy", "human", True, True)
    assert cto.may("tick") and cto.may("merge")


def test_legacy_worker_is_not_a_lead(approved_store: Path) -> None:
    context = context_for(approved_store)

    worker = resolve_identity(context, actor="implementer", session=None)

    assert (worker.kind, worker.human, worker.lead) == ("legacy", False, False)
    assert not worker.may("tick")
    assert worker.may("merge")


def test_legacy_ignores_a_session_token(approved_store: Path) -> None:
    context = context_for(approved_store)

    identity = resolve_identity(context, actor="cto", session=UNKNOWN)

    assert identity.kind == "legacy"
    assert identity.session is None


def test_a_session_resolves_its_seat(role_store: SimpleNamespace) -> None:
    context = context_for(role_store.store)
    token = open_test_session(role_store.store, role_store.orders_seat)

    identity = resolve_identity(context, actor=None, session=token)

    assert identity.kind == "seat"
    assert identity.name == role_store.orders_seat
    assert identity.seat is not None and identity.seat.anchor == role_store.orders
    assert identity.role is not None and identity.role.name == "sprint-lead"
    assert identity.human is False
    assert identity.session is not None and identity.session != token


def test_the_lead_role_may_tick_and_a_sprint_lead_may_not(
    role_store: SimpleNamespace,
) -> None:
    context = context_for(role_store.store)
    lead = resolve_identity(
        context, actor=None, session=open_test_session(role_store.store, role_store.lead)
    )
    manager = resolve_identity(
        context,
        actor=None,
        session=open_test_session(role_store.store, role_store.orders_seat),
    )

    assert lead.lead and lead.may("tick")
    assert not manager.lead and not manager.may("tick")
    assert manager.may("update")
    assert not manager.may("relocate")


def test_no_session_is_anonymous(role_store: SimpleNamespace) -> None:
    identity = resolve_identity(context_for(role_store.store), actor=None, session=None)

    assert identity == Identity(kind="anonymous", name="anonymous")
    assert not identity.human and not identity.lead
    assert not identity.may("update")


def test_an_actor_flag_is_refused_in_role_mode(role_store: SimpleNamespace) -> None:
    with pytest.raises(IdentityRefused) as raised:
        resolve_identity(context_for(role_store.store), actor="cto", session=None)

    assert raised.value.code == "PERMISSION_DENIED"
    assert raised.value.details["reason"] == "actor_flag_refused"

    result = invoke("seat list", {}, store=role_store.store, actor="human")
    assert refusal(result) == ("PERMISSION_DENIED", "actor_flag_refused")


def test_rail_actor_in_the_environment_is_ignored_in_role_mode(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")

    identity = resolve_identity(context_for(role_store.store), actor=None, session=None)
    approval = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "me"},
        store=role_store.store,
    )

    assert identity.kind == "anonymous"
    assert not is_ok(approval)
    assert approval["error"]["code"] == "PERMISSION_DENIED"


def test_the_human_channel_is_the_human(role_store: SimpleNamespace) -> None:
    with as_human():
        identity = resolve_identity(
            context_for(role_store.store), actor=None, session=None, human_channel=True
        )

    assert (identity.kind, identity.name, identity.human, identity.lead) == (
        "human",
        "human",
        True,
        True,
    )
    assert identity.may("anything")


def test_an_unknown_session_is_refused_never_anonymous(
    role_store: SimpleNamespace,
) -> None:
    with caller_identity(None, None, session=UNKNOWN):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")
    assert "rail seat open" in result["error"]["details"]["command"]


def test_an_expired_session_is_refused(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat, ttl_s=1, now=1.0)

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")


def test_a_revoked_seat_keeps_no_session(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)
    with caller_identity(None, None, session=token):
        assert is_ok(invoke("seat list", {}, store=role_store.store))

    write_record(
        role_store.store,
        "rev-orders",
        SEAT_REVOKED,
        {"schema": 1, "seat": role_store.orders_seat, "revoked_by": "human"},
        at="2026-11-10T11:00:00Z",
        actor="human",
    )
    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")


def test_revoking_the_granter_ends_the_granted_seats_session(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)
    write_record(
        role_store.store,
        "rev-lead",
        SEAT_REVOKED,
        {"schema": 1, "seat": role_store.lead, "revoked_by": "human"},
        at="2026-11-10T11:00:00Z",
        actor="human",
    )

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")


def test_a_session_of_a_seat_regranted_with_another_role_is_refused(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(
        role_store.store,
        role_store.orders_seat,
        role="sprint-lead",
        anchor=role_store.orders,
    )
    write_record(
        role_store.store,
        "rev-orders",
        SEAT_REVOKED,
        {"schema": 1, "seat": role_store.orders_seat, "revoked_by": "human"},
        at="2026-11-10T11:00:00Z",
        actor="human",
    )
    write_record(
        role_store.store,
        "seat-orders-again",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": role_store.orders_seat,
            "role": "sprint-lead",
            "anchor": role_store.billing,
            "granted_by": role_store.lead,
        },
        at="2026-11-10T12:00:00Z",
        actor=role_store.lead,
    )

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "session_invalid")


def test_a_seat_whose_role_left_the_pack_is_refused(role_store: SimpleNamespace) -> None:
    write_record(
        role_store.store,
        "seat-ghost",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "ghost-1",
            "role": "ghost",
            "anchor": "project",
            "granted_by": "human",
        },
        at="2026-11-10T11:00:00Z",
        actor="human",
    )
    token = open_test_session(role_store.store, "ghost-1")

    with caller_identity(None, None, session=token):
        result = invoke("seat list", {}, store=role_store.store)

    assert refusal(result) == ("PERMISSION_DENIED", "seat_role_unknown")


def test_a_run_session_is_the_run_seat(role_store: SimpleNamespace) -> None:
    context = context_for(role_store.store)
    token = open_test_session(
        role_store.store,
        "implementer@run-1",
        run="run-1",
        role="implementer",
        anchor=role_store.o1,
        profile=None,
        granted_by=role_store.orders_seat,
    )

    identity = resolve_identity(context, actor=None, session=token)

    assert identity.kind == "seat"
    assert identity.name == "implementer@run-1"
    assert identity.role is not None and identity.role.name == "implementer"
    assert identity.seat is not None
    assert identity.seat.anchor == role_store.o1
    assert identity.seat.granted_by == role_store.orders_seat


def test_a_run_session_ends_with_the_seat_that_dispatched_it(
    role_store: SimpleNamespace,
) -> None:
    token = open_test_session(
        role_store.store,
        "implementer@run-1",
        run="run-1",
        role="implementer",
        anchor=role_store.o1,
        profile=None,
        granted_by=role_store.orders_seat,
    )
    write_record(
        role_store.store,
        "rev-orders",
        SEAT_REVOKED,
        {"schema": 1, "seat": role_store.orders_seat, "revoked_by": "human"},
        at="2026-11-10T11:00:00Z",
        actor="human",
    )

    with pytest.raises(IdentityRefused) as raised:
        resolve_identity(context_for(role_store.store), actor=None, session=token)

    assert raised.value.details["reason"] == "session_invalid"


def test_a_session_call_is_recorded_under_the_seat(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)

    with caller_identity(None, None, session=token):
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
    assert role_store.orders_seat in actors


def test_context_reach_follows_the_seat(role_store: SimpleNamespace) -> None:
    context = context_for(role_store.store)
    token = open_test_session(role_store.store, role_store.orders_seat)
    context.identity = resolve_identity(context, actor=None, session=token)

    reach = context.reach

    assert reach is not None
    assert reach.can_write(role_store.o1)
    assert not reach.can_write(role_store.b1)


def test_context_reach_is_unrestricted_for_a_legacy_name(approved_store: Path) -> None:
    legacy = context_for(approved_store)
    legacy.identity = resolve_identity(legacy, actor="cto", session=None)

    assert legacy.reach is None


def test_context_reach_is_unrestricted_for_the_human(role_store: SimpleNamespace) -> None:
    terminal = context_for(role_store.store)
    terminal.identity = resolve_identity(
        terminal, actor=None, session=None, human_channel=True
    )

    assert terminal.reach is None


def test_an_unresolved_role_mode_context_is_anonymous(role_store: SimpleNamespace) -> None:
    context = context_for(role_store.store)

    assert context.identity.kind == "anonymous"


def test_profile_comes_from_the_seat_in_role_mode(role_store: SimpleNamespace) -> None:
    context = context_for(role_store.store)
    token = open_test_session(role_store.store, role_store.orders_seat)
    context.identity = resolve_identity(context, actor=None, session=token)

    assert context.profile is None
