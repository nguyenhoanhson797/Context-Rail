"""The UI reads one snapshot and projects the compiled model."""

from __future__ import annotations

import copy
from importlib import resources
from types import SimpleNamespace

import pytest
import yaml

from context_rail.ui import api
from helpers import init_store, write_pack

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


@pytest.mark.parametrize(
    ("pack", "board_preset", "plan_preset"),
    [
        ("scrum", "story", "sprint"),
        ("video", "shot", "scene"),
        ("waterfall", "deliverable", "phase"),
        ("flow", "story", "epic"),
        ("basic", "task", "goal"),
    ],
)
def test_a_screen_pins_its_population_and_grouping(
    tmp_path, pack: str, board_preset: str, plan_preset: str
) -> None:
    store = init_store(tmp_path / pack, pack)
    document = api.context_document(store)
    screens = {screen["id"]: screen for screen in document["operating"]["screens"]}

    assert screens["board"]["preset"] == board_preset
    assert screens["board"]["group_by"] == "status"
    assert screens["plan"]["preset"] == plan_preset


@pytest.mark.parametrize(
    ("pack", "title"),
    [
        ("scrum", "Summary"),
        ("waterfall", "Summary"),
        ("video", "Production"),
        ("flow", "Summary"),
        ("basic", "Summary"),
    ],
)
def test_the_summary_screen_carries_the_pack_title(tmp_path, pack: str, title: str) -> None:
    store = init_store(tmp_path / pack, pack)
    document = api.context_document(store)
    screens = {screen["kind"]: screen for screen in document["operating"]["screens"]}

    assert screens["summary"]["title"] == title


def test_declared_actions_expose_their_preconditions(
    operating_scrum_store: SimpleNamespace,
) -> None:
    document = api.context_document(operating_scrum_store.store)
    actions = {action["name"]: action for action in document["operating"]["actions"]}

    assert actions["close-sprint"]["requires"]["children"] is True
    assert actions["start-sprint"]["requires"]["children"] is False
    assert actions["file-bug"]["on"] == "*"


def test_only_the_video_pack_declares_execution(
    operating_video_store: SimpleNamespace,
    operating_scrum_store: SimpleNamespace,
) -> None:
    video = api.context_document(operating_video_store.store)
    assert video["operating"]["execution"]["actions"] == ["render"]
    assert any(action["name"] == "render" for action in video["operating"]["actions"])

    scrum = api.context_document(operating_scrum_store.store)
    assert scrum["operating"]["execution"]["actions"] == []


@pytest.mark.parametrize("pack", ["flow", "basic"])
def test_the_absence_packs_have_no_gate_execution_or_focus(
    tmp_path, pack: str
) -> None:
    store = init_store(tmp_path / pack, pack)
    document = api.context_document(store)
    operating = document["operating"]

    assert all(not (workflow.get("quality") or {}) for workflow in document["workflows"])
    assert operating["execution"]["actions"] == []
    assert operating["focus_policy"] is None
    assert any(level["required"] is False for level in operating["hierarchy"])


@pytest.mark.parametrize(
    ("fixture", "ordered"),
    [
        ("operating_video_store", True),
        ("operating_waterfall_store", True),
        ("operating_scrum_store", False),
    ],
)
def test_ordered_hierarchy_is_a_declared_fact(
    request: pytest.FixtureRequest, fixture: str, ordered: bool
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)
    document = api.context_document(case.store)
    levels = document["operating"]["hierarchy"]

    assert bool(levels[0].get("order")) is ordered


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
