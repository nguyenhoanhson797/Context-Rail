"""The UI's reads work in a role store, where a named actor is refused."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import as_human


def test_health_reads_in_a_role_store(role_store: SimpleNamespace) -> None:
    from context_rail.ui import api

    with as_human():
        health = api.health_document(role_store.store)

    assert health["state"] in ("ok", "unknown"), health


def test_context_reads_in_a_role_store(role_store: SimpleNamespace) -> None:
    from context_rail.ui import api

    with as_human():
        context = api.context_document(role_store.store)

    assert context["pack"] == "scrum", context


def test_doc_reads_in_a_role_store(role_store: SimpleNamespace) -> None:
    from context_rail.ui import api

    with as_human():
        doc = api.doc_document(role_store.store, role_store.o1)

    assert doc["status"] == "ok", doc
    assert doc["doc"]["id"] == role_store.o1, doc


def test_portfolio_reads_in_a_role_store(role_store: SimpleNamespace) -> None:
    from context_rail.ui import api

    with as_human():
        portfolio = api.portfolio_document(role_store.store)

    assert "projects" in portfolio, portfolio


def test_the_snapshot_omits_documents_outside_the_seats_reach(
    role_store: SimpleNamespace,
) -> None:
    from context_rail.gateway.execution import caller_identity
    from context_rail.ui import api
    from helpers import open_test_session

    token = open_test_session(role_store.store, role_store.orders_seat)
    with caller_identity(None, None, session=token):
        snapshot = api.context_document(role_store.store)

    shown = {doc["id"] for group in ("phases", "tasks") for doc in snapshot[group]}
    assert role_store.orders in shown, snapshot
    assert role_store.billing not in shown, snapshot
