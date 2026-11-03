"""The compiled surface: catalog, card and explain stay bounded and honest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from context_rail.context import surface
from context_rail.core.util import dump_frontmatter
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import init_pack_store, nested_pack_document

STAMP = "2026-10-28T00:00:00Z"


def _store(tmp_path: Path) -> tuple[Path, Context]:
    document = nested_pack_document("bugs")
    document["statuses"] = {
        "todo": "not_started",
        "reported": "not_started",
        "fixing": "active",
        "verifying": "active",
        "blocked": "blocked",
        "closed": "done",
    }
    document["initial"] = "todo"
    document["transitions"] = {"todo": ["reported"], "reported": ["fixing"]}
    document["workflows"] = {
        "bugfix": {
            "initial": "reported",
            "transitions": {
                "todo": ["reported"],
                "reported": ["fixing", "closed"],
                "fixing": ["verifying", "reported"],
                "verifying": ["closed", "fixing"],
                "closed": ["fixing"],
                "blocked": ["fixing"],
            },
            "quality": {
                "closed": {"gate": "bug_done", "checks": ["repro-check"]}
            },
        },
        "sprint": {
            "initial": "todo",
            "transitions": {"todo": ["fixing"], "fixing": ["closed"], "closed": []},
        },
    }
    document["presets"]["bug"] = {
        "primitive": "item",
        "tags": ["bug"],
        "workflow": "bugfix",
        "container": False,
    }
    document["presets"]["task"]["workflow"] = "sprint"
    document["presets"]["sprint"]["workflow"] = "sprint"
    store = init_pack_store(tmp_path, document)
    charter = {
        "primitive": "policy",
        "preset": "charter",
        "title": "Charter",
        "tags": [],
        "created": STAMP,
        "updated": STAMP,
        "schema": 1,
        "project": {"goal": "ship", "vision": "v", "scope": [], "wont": []},
        "protected_files": [],
        "gates": {
            "item_done": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": False,
                "require_human": False,
            },
            "scope_done": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": False,
                "require_human": False,
            },
            "merge": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": True,
                "require_human": False,
            },
            "release": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": True,
                "require_human": True,
            },
            "bug_done": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": False,
                "require_human": False,
            },
        },
        "default_branch": "main",
    }
    (store / "charter.md").write_text(
        "---\n" + dump_frontmatter(charter) + "---\n# Charter\n",
        encoding="utf-8",
        newline="\n",
    )
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("bugs", store),
        actor="cto",
    )
    return store, context


def _add(store: Path, preset: str, title: str) -> None:
    created = invoke("add", {"preset": preset, "title": title}, store=store, actor="cto")
    assert created["ok"], created


def _bug_at(store: Path, status: str) -> None:
    path = store / "items" / "bug" / "a-bug.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    front: dict[str, Any] = {
        "primitive": "item",
        "preset": "bug",
        "title": "A bug",
        "status": status,
        "tags": ["bug"],
        "created": STAMP,
        "updated": STAMP,
    }
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n# A bug\n",
        encoding="utf-8",
        newline="\n",
    )


def test_the_catalog_names_every_preset_and_its_workflow(tmp_path: Path) -> None:
    _store_path, context = _store(tmp_path)

    catalog = surface.catalog(context)

    names = {entry["name"] for entry in catalog["items"]}
    assert {"bug", "task", "phase", "sprint"} <= names
    bug = next(entry for entry in catalog["items"] if entry["name"] == "bug")
    assert bug["workflow"] == "bugfix"
    assert bug["container"] is False
    assert bug["use_when"]


def test_the_card_names_the_workflow_state_next_and_proof(tmp_path: Path) -> None:
    store, context = _store(tmp_path)
    _bug_at(store, "closed")
    context.index.sync()

    card = surface.card(context, "a-bug")

    assert card["workflow"] == "bugfix"
    assert card["state"] == "closed"
    assert card["gate"] == "bug_done"
    assert card["proof"]["checks"] == ["repro-check"]
    assert {"fixing", "verifying", "blocked"} <= set(card["states"])
    assert card["command"].startswith("rail change move a-bug")


def test_the_card_never_leaks_author_concepts(tmp_path: Path) -> None:
    store, context = _store(tmp_path)
    _bug_at(store, "fixing")
    context.index.sync()

    text = repr(surface.card(context, "a-bug")).lower()

    for forbidden in ("category", "placement", "pack.yaml", "pack:"):
        assert forbidden not in text


def test_a_container_card_requires_finished_children(tmp_path: Path) -> None:
    store, context = _store(tmp_path)
    _add(store, "sprint", "Sprint 3")
    context.index.sync()

    card = surface.card(context, "sprint-3")

    assert card["container"] is True
    assert card["proof"]["children"] == "done or dropped"


def test_explain_returns_the_full_definition_and_refuses_an_unknown(
    tmp_path: Path,
) -> None:
    _store_path, context = _store(tmp_path)

    workflow = surface.explain(context, "bugfix")
    preset = surface.explain(context, "bug")

    assert workflow["kind"] == "workflow"
    assert workflow["initial"] == "reported"
    assert workflow["quality"]["closed"]["gate"] == "bug_done"
    assert preset["kind"] == "preset"
    assert preset["workflow"] == "bugfix"

    with pytest.raises(surface.SurfaceError) as caught:
        surface.explain(context, "ghost")
    assert caught.value.code == "NOT_FOUND"


def test_a_worker_catalog_is_scoped_to_its_item(tmp_path: Path) -> None:
    store, context = _store(tmp_path)
    _bug_at(store, "fixing")
    context.index.sync()

    assert surface.catalog(context, role="implementer") == {"items": [], "omitted": 0}
    scoped = surface.catalog(context, role="implementer", item="a-bug")
    assert [entry["name"] for entry in scoped["items"]] == ["bug"]


def test_two_workflows_offer_different_next_states(tmp_path: Path) -> None:
    store, context = _store(tmp_path)
    _add(store, "bug", "A bug")
    _add(store, "task", "A task")
    context.index.sync()

    bug = surface.card(context, "a-bug")
    task = surface.card(context, "a-task")

    assert bug["workflow"] == "bugfix"
    assert bug["next"] == ["reported"]
    assert task["workflow"] == "sprint"
    assert task["next"] == ["fixing"]


def test_the_work_order_sections_put_the_workflow_after_the_objective() -> None:
    from context_rail.runner.droid import WORK_ORDER_SECTIONS

    assert WORK_ORDER_SECTIONS[:2] == ("objective", "workflow")


def test_the_cto_objective_is_queue_driven() -> None:
    from context_rail.gateway.handlers.cto import _ORDER_OBJECTIVE

    assert "card" in _ORDER_OBJECTIVE
    assert "Plan executable leaf Items and link them" not in _ORDER_OBJECTIVE


def test_the_surface_is_deterministic_and_bounded(tmp_path: Path) -> None:
    _store_path, context = _store(tmp_path)

    assert surface.catalog(context) == surface.catalog(context)
    bounded = surface.catalog(context, limit=200)
    assert bounded["omitted"] > 0
