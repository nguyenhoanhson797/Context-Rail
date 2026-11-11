"""The human boundary and the seat scope hold against wire-level callers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.store.sessions import open_session
from helpers import (
    as_human,
    daemon_descriptor,
    daemon_request,
    open_test_session,
    serving_daemon,
)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def grant_worker(store: Path, anchor: str) -> None:
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "worker-1", "role": "implementer", "anchor": anchor},
            store=store,
            actor=None,
        )
    assert is_ok(granted), granted


def test_anonymous_cannot_start_the_ui_human_channel(
    role_store: SimpleNamespace,
) -> None:
    with caller_identity(None, None, session=None):
        served = invoke("ui serve", {"port": 0}, store=role_store.store)

    assert refusal(served)[0] == "PERMISSION_DENIED"


def test_the_terminal_human_may_start_the_ui_channel(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(
        "context_rail.ui.server.serve",
        lambda store, *, actor, port: calls.append((store, actor, port)),
    )
    with as_human():
        served = invoke("ui serve", {"port": 0}, store=role_store.store, actor=None)

    assert is_ok(served), served
    assert calls and calls[0][1] == "human"


def test_a_daemon_request_cannot_claim_a_terminal(role_store: SimpleNamespace) -> None:
    with serving_daemon(role_store.store):
        descriptor = daemon_descriptor(role_store.store)
        assert descriptor is not None
        reply = daemon_request(
            descriptor["port"],
            {
                "tool": "seat revoke",
                "params": {"seat": role_store.billing_seat},
                "tty": True,
                "token": descriptor["token"],
            },
        )

    assert not reply["ok"], reply
    assert reply["error"]["code"] == "SESSION_REQUIRED"
    with as_human():
        listed = invoke("seat list", {}, store=role_store.store, actor=None)
    assert is_ok(listed), listed
    assert role_store.billing_seat in {row["seat"] for row in listed["value"]["seats"]}


def test_anonymous_reads_no_store_documents(role_store: SimpleNamespace) -> None:
    with caller_identity(None, None, session=None):
        shown = invoke("show", {"slug": role_store.b1}, store=role_store.store)

    assert refusal(shown) == ("PERMISSION_DENIED", "out_of_reach")


def test_a_lens_never_widens_the_callers_read_reach(
    role_store: SimpleNamespace,
) -> None:
    grant_worker(role_store.store, role_store.o1)
    token = open_test_session(role_store.store, "worker-1")
    with caller_identity(None, None, session=token, lens=role_store.orders_seat):
        shown = invoke("show", {"slug": role_store.o2}, store=role_store.store)

    assert refusal(shown) == ("PERMISSION_DENIED", "out_of_reach")


def test_revoke_closes_the_seat_session_and_a_regrant_does_not_revive_it(
    role_store: SimpleNamespace,
) -> None:
    grant_worker(role_store.store, role_store.o1)
    token = open_test_session(role_store.store, "worker-1")
    with caller_identity(None, None, session=token):
        first = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert is_ok(first), first

    with as_human():
        revoked = invoke(
            "seat revoke", {"seat": "worker-1"}, store=role_store.store, actor=None
        )
    assert is_ok(revoked), revoked
    assert revoked["value"]["closed"] == 1
    with caller_identity(None, None, session=token):
        after = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert refusal(after) == ("PERMISSION_DENIED", "session_invalid")

    grant_worker(role_store.store, role_store.o1)
    with caller_identity(None, None, session=token):
        regranted = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert refusal(regranted) == ("PERMISSION_DENIED", "session_invalid")


def test_a_session_opened_before_its_grant_is_refused(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    _token, session = open_session(
        store,
        "worker-1",
        role="implementer",
        anchor="o1",
        profile="worker",
        granted_by="lead-1",
        now=100.0,
    )
    assert session.created == 100.0

    from context_rail.kernel.seats import LiveSeat

    seat = LiveSeat(
        seat="worker-1",
        role="implementer",
        anchor="o1",
        profile="worker",
        granted_by="lead-1",
        granted_at="2026-11-11T10:00:00Z",
    )
    from context_rail.gateway.identity import _opened_before_grant

    assert _opened_before_grant(session, seat.granted_at) is True
