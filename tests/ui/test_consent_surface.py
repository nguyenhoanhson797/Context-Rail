"""The UI snapshot exposes a consent precondition and a recorded consent."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.ui import api
from helpers import init_store


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]


def test_the_ui_sees_the_consent_precondition_and_the_recorded_words(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path, pack="adaptive-contract")
    added = invoke(
        "add",
        {"preset": "critical-change", "title": "Rotate the signing key"},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]
    contracted = invoke(
        "do",
        {
            "action": "contract-critical",
            "item": slug,
            "etag": _etag(store, slug),
            "set": [],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(contracted), contracted

    snapshot = api.context_document(store)
    action = next(
        entry
        for entry in snapshot["operating"]["actions"]
        if entry["name"] == "approve-critical"
    )
    assert action["requires"]["consent"] is True

    recorded = invoke(
        "consent",
        {
            "item": slug,
            "on_behalf_of": "Ada",
            "text": "Yes, rotate it after the backup lands.",
            "expect": _etag(store, slug),
        },
        store=store,
        actor="cto",
    )
    assert is_ok(recorded), recorded

    shown = api.doc_document(store, slug)
    assert shown["consent"]["text"] == "Yes, rotate it after the backup lands."
    assert shown["consent"]["on_behalf_of"] == "Ada"
    assert shown["consent"]["current"] is True
