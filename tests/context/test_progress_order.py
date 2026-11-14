"""Progress groups keep plan order, not alphabetical order.

The Summary runway is labelled "in plan order" and the queue ranks work along
the same hierarchy order. ``created`` carries second granularity, so containers
added in one burst tie on it. Breaking that tie by slug silently reorders the
plan: "Sprint three" sorts before "Sprint two", and the runway then shows a
later sprint ahead of the active one. Insertion order is the plan order the
store actually recorded, so it wins the tie.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml
from helpers import compiled_model, init_store, write_pack

from context_rail.context import operating
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack


def _add(store: Path, preset: str, title: str, parent: str | None = None) -> str:
    args: dict[str, object] = {"preset": preset, "title": title}
    if parent is not None:
        args["rel"] = [f"part_of={parent}"]
    result = invoke("add", args, store=store, actor="cto")
    assert result["ok"] is True, result
    value = result["value"]
    return value.get("slug") or value["doc"]["slug"]


@pytest.fixture
def burst_store(tmp_path: Path, fixed_clock: str) -> Path:
    """Three sprints added in one clock tick, so ``created`` cannot separate them."""
    store = init_store(tmp_path / "plan", "scrum")
    for name in ("Sprint one", "Sprint two", "Sprint three"):
        sprint = _add(store, "sprint", name)
        _add(store, "story", f"Work in {name}", sprint)
    return store


def test_groups_created_in_one_burst_keep_insertion_order(burst_store: Path) -> None:
    context = Context(
        store=burst_store,
        manifest=load_manifest(burst_store),
        pack=load_pack("scrum", burst_store),
        actor="cto",
    )
    try:
        view = operating.resolve_progress(context, compiled_model(burst_store, "scrum"))
    finally:
        context.close()
    assert [child["slug"] for child in view.children] == [
        "sprint-one",
        "sprint-two",
        "sprint-three",
    ]


def test_groups_created_in_one_burst_keep_insertion_order_after_reindex(
    tmp_path: Path, fixed_clock: str
) -> None:
    store = init_store(tmp_path / "reindexed-plan", "scrum")
    for name in ("Sprint two", "Sprint one", "Sprint three"):
        sprint = _add(store, "sprint", name)
        _add(store, "story", f"Work in {name}", sprint)
    assert invoke("reindex", {"full": True}, store=store, actor="human")["ok"] is True

    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("scrum", store),
        actor="cto",
    )
    try:
        view = operating.resolve_progress(context, compiled_model(store, "scrum"))
    finally:
        context.close()
    assert [child["slug"] for child in view.children] == [
        "sprint-two",
        "sprint-one",
        "sprint-three",
    ]


def test_an_explicit_order_still_wins(tmp_path: Path) -> None:
    """A declared order is semantic and outranks the insertion tiebreak."""
    store = init_store(tmp_path / "ordered", "scrum")
    _add(store, "sprint", "Zulu sprint")
    _add(store, "sprint", "Alpha sprint")
    document = yaml.safe_load(
        resources.files("context_rail")
        .joinpath("packs", "scrum", "pack.yaml")
        .read_text(encoding="utf-8")
    )
    document["operating"]["hierarchy"][0]["order"] = "title"
    write_pack(store, "scrum", document)

    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("scrum", store),
        actor="cto",
    )
    try:
        view = operating.resolve_progress(context, compiled_model(store, "scrum"))
    finally:
        context.close()
    assert [child["slug"] for child in view.children] == ["alpha-sprint", "zulu-sprint"]
