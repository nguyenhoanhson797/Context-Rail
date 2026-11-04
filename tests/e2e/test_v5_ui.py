"""The UI reads one snapshot and projects the compiled model."""

from __future__ import annotations

import copy
from importlib import resources
from types import SimpleNamespace

import pytest
import yaml

from context_rail.ui import api
from helpers import write_pack

_SCREEN_TITLES = {
    "scrum": ("Sprint board", "Sprints"),
    "waterfall": ("Deliverables", "Phases"),
    "video": ("Shots", "Scenes"),
}


@pytest.mark.parametrize(
    ("fixture", "pack"),
    [
        ("operating_scrum_store", "scrum"),
        ("operating_waterfall_store", "waterfall"),
        ("operating_video_store", "video"),
    ],
)
def test_the_snapshot_labels_and_screens_match_the_model(
    request: pytest.FixtureRequest, fixture: str, pack: str
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)

    document = api.context_document(case.store)
    titles = [screen["title"] for screen in document["operating"]["screens"]]
    board, plan = _SCREEN_TITLES[pack]

    assert board in titles
    assert plan in titles
    assert document["operating"]["queue"]["items"]
    assert document["operating"]["queue"]["items"][0]["focus"] is True
    assert document["operating"]["progress"]["runs"] is None


@pytest.mark.parametrize(
    ("fixture", "gate"),
    [
        ("operating_scrum_store", "item_done"),
        ("operating_waterfall_store", "item_done"),
        ("operating_video_store", "item_done"),
    ],
)
def test_the_pack_screen_has_every_gate(
    request: pytest.FixtureRequest, fixture: str, gate: str
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)

    document = api.context_document(case.store)
    gates = {
        gate_name
        for workflow in document["workflows"]
        for gate_name in (
            rule.get("gate") for rule in (workflow.get("quality") or {}).values()
        )
        if gate_name
    }

    assert gate in gates
    assert document["digests"]["semantic"]


def test_a_label_only_change_relabels_the_ui(
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
        yaml.safe_dump(
            {
                "version": 1,
                "labels": {"shot": {"one": "Take", "many": "Takes"}},
                "screens": [{"id": "board", "kind": "board", "title": "Takes"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )

    before = api.context_document(store)
    changed = {
        "version": 1,
        "labels": {"shot": {"one": "Angle", "many": "Angles"}},
        "screens": [{"id": "board", "kind": "board", "title": "Angles"}],
    }
    surface.write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8", newline="\n"
    )
    after = api.context_document(store)

    assert before["operating"]["labels"]["shot"]["one"] == "Take"
    assert after["operating"]["labels"]["shot"]["one"] == "Angle"
    assert before["digests"]["semantic"] == after["digests"]["semantic"]
    assert before["digests"]["presentation"] != after["digests"]["presentation"]
