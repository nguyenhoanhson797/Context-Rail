"""The surface contract: complete, bounded and free of author concepts."""

from __future__ import annotations

import json
from pathlib import Path

from context_rail.context import surface
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import init_pack_store, workflow_pack_document

_FORBIDDEN = ("pack.yaml", "category", "placement", "pack:", "sha256")


def _context(tmp_path: Path) -> Context:
    store = init_pack_store(tmp_path, workflow_pack_document())
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("twoways", store),
        actor="cto",
    )


def test_every_preset_has_a_catalog_line(tmp_path: Path) -> None:
    context = _context(tmp_path)

    catalog = surface.catalog(context)

    assert {entry["name"] for entry in catalog["items"]} == set(context.pack.presets)
    assert all(entry["use_when"] for entry in catalog["items"])


def test_the_surface_never_contains_an_author_concept(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert invoke(
        "add", {"preset": "bug", "title": "A bug"}, store=context.store, actor="cto"
    )["ok"]
    context.index.sync()

    rendered = json.dumps(
        {
            "catalog": surface.catalog(context),
            "card": surface.card(context, "a-bug"),
            "explain": surface.explain(context, "bugfix"),
            "why": surface.why(context, "a-bug"),
        },
        sort_keys=True,
    ).lower()

    for word in _FORBIDDEN:
        assert word not in rendered, word


def test_a_refusal_names_the_command_that_produces_the_proof() -> None:
    from context_rail.gateway.handlers.docs import _gate_command

    command = _gate_command("a-bug", ["repro-check"])

    assert "rail check declare repro-check" in command
    assert "a-bug" in command


def test_every_surface_view_stays_inside_its_budget(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert invoke(
        "add", {"preset": "bug", "title": "A bug"}, store=context.store, actor="cto"
    )["ok"]
    context.index.sync()

    assert len(json.dumps(surface.catalog(context))) <= surface.CATALOG_CHARS + 64
    assert len(json.dumps(surface.card(context, "a-bug"))) <= surface.CARD_CHARS + 64
    assert (
        len(json.dumps(surface.explain(context, "bugfix")))
        <= surface.EXPLAIN_CHARS + 64
    )
    assert len(json.dumps(surface.verbs(context))) <= surface.VERBS_CHARS + 64
