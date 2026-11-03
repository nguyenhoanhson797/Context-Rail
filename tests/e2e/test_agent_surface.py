"""A fresh agent can read the store through the surface alone."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import init_pack_store, workflow_pack_document


def test_the_surface_reads_a_two_workflow_store(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert is_ok(
        invoke("add", {"preset": "task", "title": "A task"}, store=store, actor="cto")
    )
    assert is_ok(
        invoke("add", {"preset": "bug", "title": "A bug"}, store=store, actor="cto")
    )

    catalog = invoke("types", {}, store=store, actor="cto")
    queue = invoke("next", {}, store=store, actor="cto")
    card = invoke("show", {"slug": "a-bug"}, store=store, actor="cto")
    reason = invoke("why", {"slug": "a-bug"}, store=store, actor="cto")
    explained = invoke("explain", {"name": "bugfix"}, store=store, actor="cto")

    assert is_ok(catalog), catalog
    assert is_ok(queue), queue
    assert is_ok(card), card
    assert is_ok(reason), reason
    assert is_ok(explained), explained
    assert card["value"]["card"]["workflow"] == "bugfix"
    assert card["value"]["card"]["next"] == ["fixing"]
    assert explained["value"]["initial"] == "todo"

    text = repr(
        {
            "catalog": catalog["value"],
            "queue": queue["value"],
            "card": card["value"]["card"],
            "why": reason["value"],
            "explain": explained["value"],
        }
    ).lower()
    for forbidden in ("pack.yaml", "category", "placement"):
        assert forbidden not in text
