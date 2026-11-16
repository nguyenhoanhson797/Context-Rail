"""The surface stays cheap and bounded on a thousand-document store."""

from __future__ import annotations

import json

import pytest

from context_rail.context import surface
from context_rail.gateway.gateway import Context
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import median_ms
from perf.gen import make_store


def _context(tmp_path) -> Context:
    store = make_store(tmp_path, 1000)
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("basic", store),
        actor="cto",
    )


@pytest.mark.perf
def test_the_surface_views_are_fast_and_bounded(tmp_path) -> None:
    context = _context(tmp_path)

    catalog = surface.catalog(context)
    card = surface.card(context, "t0")
    explained = surface.explain(context, "task")

    assert len(json.dumps(catalog)) <= surface.CATALOG_CHARS + 64
    assert len(json.dumps(card)) <= surface.CARD_CHARS + 64
    assert len(json.dumps(explained)) <= surface.EXPLAIN_CHARS + 64

    assert median_ms(lambda: surface.catalog(context), 5) < 150
    assert median_ms(lambda: surface.card(context, "t0"), 5) < 80
    assert median_ms(lambda: surface.explain(context, "task"), 5) < 80


@pytest.mark.perf
def test_the_surface_views_with_result_contracts_stay_fast_and_bounded(tmp_path) -> None:
    store = make_store(
        tmp_path,
        1000,
        with_results=True,
        hot_attempts=100,
        large_selected_bytes=262144,
        with_reviews=True,
    )
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("basic", store),
        actor="cto",
    )
    try:
        catalog = surface.catalog(context)
        card = surface.card(context, "t0")
        explained = surface.explain(context, "task")

        assert len(json.dumps(catalog)) <= surface.CATALOG_CHARS + 64
        assert len(json.dumps(card)) <= surface.CARD_CHARS + 64
        assert len(json.dumps(explained)) <= surface.EXPLAIN_CHARS + 64

        cat_ms = median_ms(lambda: surface.catalog(context), 5)
        card_ms = median_ms(lambda: surface.card(context, "t0"), 5)
        exp_ms = median_ms(lambda: surface.explain(context, "task"), 5)
        print(
            f"result-enabled surface @1000+hot100: catalog {cat_ms:.1f} ms (<150), "
            f"card {card_ms:.1f} ms (<80), explain {exp_ms:.1f} ms (<80)"
        )
        assert cat_ms < 150
        assert card_ms < 80
        assert exp_ms < 80
    finally:
        context.close()
