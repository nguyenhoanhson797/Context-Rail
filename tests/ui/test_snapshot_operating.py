"""The snapshot projects the compiled model: labels, screens, focus, digests."""

from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from context_rail.gateway.gateway import Context
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.ui import api, snapshot
from helpers import write_pack

_SURFACE = {
    "version": 1,
    "labels": {"shot": {"one": "Shot", "many": "Take"}},
    "screens": [{"id": "board", "kind": "board", "title": "Shots"}],
    "fields": {"card": ["model"], "filters": ["status"], "columns": ["title"]},
}


def _context(store: Path, pack: str) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor="cto",
    )


@pytest.mark.parametrize(
    ("fixture", "pack", "unit", "container", "board"),
    [
        ("operating_scrum_store", "scrum", "story", "sprint", "Sprint board"),
        ("operating_waterfall_store", "waterfall", "deliverable", "phase", "Deliverables"),
        ("operating_video_store", "video", "shot", "scene", "Shots"),
    ],
)
def test_the_snapshot_projects_each_operating_model(
    request: pytest.FixtureRequest,
    fixture: str,
    pack: str,
    unit: str,
    container: str,
    board: str,
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)
    context = _context(case.store, pack)
    try:
        value = snapshot.build(context)
    finally:
        context.close()

    operating = value["operating"]

    assert operating["unit"]["preset"] == unit
    assert operating["hierarchy"][0]["preset"] == container
    assert operating["focus"]["state"] == "resolved"
    assert operating["queue"]["items"]
    assert operating["queue_policy"]["presets"]
    assert operating["progress"]["runs"] is None
    assert operating["labels"][unit]["one"]
    assert any(screen["title"] == board for screen in operating["screens"])
    assert value["digests"]["semantic"]
    assert value["digests"]["presentation"]
    assert value["digests"]["full"]


def test_a_label_only_change_changes_only_the_presentation_digest(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    document = copy.deepcopy(
        yaml.safe_load(
            resources.files("context_rail")
            .joinpath("packs", "video", "pack.yaml")
            .read_text(encoding="utf-8")
        )
    )
    write_pack(store, "video", document)
    surface = store / "packs" / "video" / "surface.yaml"
    surface.write_text(
        yaml.safe_dump(_SURFACE, sort_keys=False), encoding="utf-8", newline="\n"
    )
    first = _context(store, "video")
    before = snapshot.build(first)
    first.close()

    changed = copy.deepcopy(_SURFACE)
    changed["labels"]["shot"] = {"one": "Take", "many": "Takes"}
    surface.write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8", newline="\n"
    )
    second = _context(store, "video")
    after = snapshot.build(second)
    second.close()

    assert before["digests"]["semantic"] == after["digests"]["semantic"]
    assert before["digests"]["presentation"] != after["digests"]["presentation"]
    assert after["operating"]["labels"]["shot"]["one"] == "Take"


def test_the_api_context_document_exposes_no_pack_internals(
    operating_video_store: SimpleNamespace,
) -> None:
    document = api.context_document(operating_video_store.store)
    text = json.dumps(document)

    assert document["operating"]["unit"]["preset"] == "shot"
    assert "pack.yaml" not in text
    assert "policy" not in document
    assert "surface.yaml" not in text
