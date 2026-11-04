"""Dispatch respects the operating focus unless the caller passes ``--all``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.context.operating import OperatingError, outside_focus
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.handlers.dispatch.implementer import _guard_focus
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.write import WriteRefused


def _context(store: Path, pack: str) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor="cto",
    )


def test_an_item_outside_the_focus_is_blocked(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")
    model = operating_scrum_store.model

    assert outside_focus(ctx, model, operating_scrum_store.story) is None
    assert outside_focus(ctx, model, operating_scrum_store.bug) == (
        operating_scrum_store.sprint
    )


def test_the_guard_refuses_until_all_is_passed(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")

    with pytest.raises(WriteRefused) as caught:
        _guard_focus(ctx, operating_scrum_store.bug, allow_all=False)

    assert caught.value.code == "INVALID_STATE"
    assert caught.value.details["reason"] == "out_of_focus"
    assert "--all" in caught.value.details["command"]

    _guard_focus(ctx, operating_scrum_store.bug, allow_all=True)


def test_an_ambiguous_focus_refuses_every_dispatch(
    operating_scrum_store: SimpleNamespace,
) -> None:
    from context_rail.core.result import is_ok

    store = operating_scrum_store.store
    added = invoke(
        "add", {"preset": "sprint", "title": "Sprint two"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]
    etag = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]
    moved = invoke(
        "move", {"slug": slug, "status": "doing", "expect": etag}, store=store, actor="cto"
    )
    assert is_ok(moved), moved
    ctx = _context(store, "scrum")

    with pytest.raises(OperatingError) as focus:
        outside_focus(ctx, operating_scrum_store.model, operating_scrum_store.bug)
    with pytest.raises(WriteRefused) as caught:
        _guard_focus(ctx, operating_scrum_store.bug, allow_all=False)

    assert focus.value.code == "AMBIGUOUS_FOCUS"
    assert caught.value.code == "AMBIGUOUS_FOCUS"
