"""The rights check sees the charter's real approval state."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke

PROTECTED = "send-invoice"


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _update(store: Path, slug: str, *, actor: str, set_: list[str]) -> dict:
    return invoke(
        "update",
        {"slug": slug, "set": set_, "expect": _etag(store, slug)},
        store=store,
        actor=actor,
    )


def test_a_protected_write_in_an_unapproved_charter_is_refused_by_rights(
    unapproved_store: Path,
) -> None:
    shown = invoke("show", {"slug": "charter"}, store=unapproved_store)["value"]
    assert shown["approval"]["state"] == "missing"

    refused = _update(
        unapproved_store,
        PROTECTED,
        actor="cto",
        set_=["title=Send the invoice"],
    )

    assert not is_ok(refused), refused
    error = refused["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert "approved" in error["message"]


def test_an_approved_store_behaves_as_before(approved_store: Path) -> None:
    refused = _update(
        approved_store,
        PROTECTED,
        actor="cto",
        set_=["title=Send the invoice"],
    )

    assert not is_ok(refused), refused
    error = refused["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["decision"] == "human-only"

    added = invoke(
        "add",
        {"preset": "task", "title": "Approved-store task"},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(added), added
