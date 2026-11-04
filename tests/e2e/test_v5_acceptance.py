"""The v5 acceptance matrix: one engine, three operating models."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import init_store


def _show(store: Path, slug: str) -> dict:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]


def _add(store: Path, preset: str, title: str, **extra: object) -> dict:
    args = {"preset": preset, "title": title, **extra}
    return invoke("add", args, store=store, actor="cto")


def _move(store: Path, slug: str, status: str) -> None:
    etag = _show(store, slug)["doc"]["etag"]
    moved = invoke(
        "move", {"slug": slug, "status": status, "expect": etag}, store=store, actor="cto"
    )
    assert is_ok(moved), moved


@pytest.mark.parametrize(
    ("fixture", "preset", "focus_attribute", "expected_unit"),
    [
        ("operating_scrum_store", "story", "sprint", "story"),
        ("operating_waterfall_store", "deliverable", "phase", "deliverable"),
        ("operating_video_store", "shot", "scene", "shot"),
    ],
)
def test_the_unit_is_the_default_parent(
    request: pytest.FixtureRequest,
    fixture: str,
    preset: str,
    focus_attribute: str,
    expected_unit: str,
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)
    focus = getattr(case, focus_attribute)

    added = _add(case.store, preset, f"A new {preset}")

    assert is_ok(added), added
    shown = _show(case.store, added["value"]["slug"])
    assert shown["out"]["part_of"] == [focus]
    assert shown["card"]["unit"] == expected_unit


def test_an_unrelated_preset_stays_unparented(
    operating_video_store: SimpleNamespace,
) -> None:
    added = _add(operating_video_store.store, "asset", "A plate")

    assert is_ok(added), added
    shown = _show(operating_video_store.store, added["value"]["slug"])
    assert not shown["out"].get("part_of")


def test_an_explicit_relation_wins(
    operating_scrum_store: SimpleNamespace,
) -> None:
    other = _add(operating_scrum_store.store, "sprint", "Sprint two")
    assert is_ok(other), other

    added = _add(
        operating_scrum_store.store,
        "story",
        "Elsewhere",
        rel=[f"part_of={other['value']['slug']}"],
    )

    assert is_ok(added), added
    shown = _show(operating_scrum_store.store, added["value"]["slug"])
    assert shown["out"]["part_of"] == [other["value"]["slug"]]


def test_add_without_a_focus_refuses_and_names_the_relation(tmp_path: Path) -> None:
    store = init_store(tmp_path / "quiet", "scrum")
    _add(store, "sprint", "Not started")

    refused = _add(store, "story", "Orphan")

    assert refused["ok"] is False
    assert refused["error"]["code"] == "NO_FOCUS"
    assert "part_of" in refused["error"]["details"]["rel"]


@pytest.mark.parametrize(
    ("fixture", "unit"),
    [
        ("operating_scrum_store", "story"),
        ("operating_waterfall_store", "deliverable"),
        ("operating_video_store", "shot"),
    ],
)
def test_next_returns_the_operating_queue(
    request: pytest.FixtureRequest, fixture: str, unit: str
) -> None:
    case: SimpleNamespace = request.getfixturevalue(fixture)

    value = invoke("next", {}, store=case.store, actor="cto")["value"]

    assert value["focus"]["state"] == "resolved"
    assert value["items"]
    assert value["items"][0]["focus"] is True
    assert unit in {item["preset"] for item in value["items"]}
    assert value["scope"] == "focus"


def test_next_all_reaches_outside_the_queue(
    operating_scrum_store: SimpleNamespace,
) -> None:
    value = invoke(
        "next", {"all": True}, store=operating_scrum_store.store, actor="cto"
    )["value"]

    assert value["scope"] == "all"
    assert operating_scrum_store.sprint in value["queue"]


def test_next_into_overrides_the_focus(
    operating_waterfall_store: SimpleNamespace,
) -> None:
    value = invoke(
        "next",
        {"into": operating_waterfall_store.next_phase},
        store=operating_waterfall_store.store,
        actor="cto",
    )["value"]

    assert value["focus"]["resolved_by"] == "into"
    assert value["focus"]["focus"]["slug"] == operating_waterfall_store.next_phase
    assert value["queue"][0] == operating_waterfall_store.other_deliverable
    assert value["scope"] == "focus"


def test_status_carries_unit_focus_groups_and_progress(
    operating_waterfall_store: SimpleNamespace,
) -> None:
    value = invoke("status", {}, store=operating_waterfall_store.store, actor="cto")[
        "value"
    ]

    assert value["counts"]
    assert value["unit"]["preset"] == "deliverable"
    assert value["focus"]["state"] == "resolved"
    assert value["groups"][0]["preset"] == "phase"
    assert [row["slug"] for row in value["groups"][0]["items"]] == [
        operating_waterfall_store.phase,
        operating_waterfall_store.next_phase,
    ]
    assert value["progress"]["total"] == 2
    assert value["labels"]["phase"]["many"] == "Phases"
    assert value["queue"]["items"]


def test_brief_headline_names_the_focus(
    operating_video_store: SimpleNamespace,
) -> None:
    value = invoke("cto brief", {}, store=operating_video_store.store, actor="cto")[
        "value"
    ]

    assert value["text"].startswith("Focus: Act one")
    assert value["state"]["unit"]["preset"] == "shot"
    assert value["state"]["progress"]["total"] == 2
    assert value["state"]["progress"]["runs"] is None


def test_show_card_adds_the_operating_fields(
    operating_video_store: SimpleNamespace,
) -> None:
    shown = _show(operating_video_store.store, operating_video_store.shot)
    card = shown["card"]

    assert card["preset_label"] == "Shot"
    assert card["unit"] == "shot"
    assert card["focus"]["slug"] == operating_video_store.scene
    assert card["operating"]["role"] == "unit"
    assert "storyboard" in card["actions"]


def test_the_model_command_returns_the_ir(
    operating_video_store: SimpleNamespace,
) -> None:
    value = invoke("model", {}, store=operating_video_store.store, actor="cto")["value"]

    assert value["neutral"] is False
    assert value["model"]["unit"] == "shot"
    assert value["model"]["queue"]["presets"] == ["shot"]
    assert value["unit"]["workflow"] == "shot"
    assert value["focus"]["state"] == "resolved"
    assert value["digests"]["semantic"]
    assert value["digests"]["presentation"]
    assert value["digests"]["full"]


def test_a_neutral_pack_reports_a_neutral_model(tmp_path: Path) -> None:
    from helpers import init_pack_store, nested_pack_document

    document = nested_pack_document("plain")
    document["version"] = 1
    store = init_pack_store(tmp_path / "plain", document)

    value = invoke("model", {}, store=store, actor="cto")["value"]

    assert value["neutral"] is True
    assert value["model"]["unit"] is None
    assert value["focus"]["state"] == "none"


def test_status_never_refuses_an_ambiguous_focus(
    operating_scrum_store: SimpleNamespace,
) -> None:
    second = _add(operating_scrum_store.store, "sprint", "Sprint two")
    _move(operating_scrum_store.store, second["value"]["slug"], "doing")

    value = invoke("status", {}, store=operating_scrum_store.store, actor="cto")["value"]

    assert value["focus"]["state"] == "ambiguous"
    assert value["queue"]["items"] == []
    assert value["queue"]["error"] == "AMBIGUOUS_FOCUS"


def test_the_cli_exposes_the_new_next_flags_and_model(
    operating_video_store: SimpleNamespace,
) -> None:
    import json

    from helpers import run_cli

    cwd = operating_video_store.store.parent
    store = operating_video_store.store

    for args in (
        ("next", "--focus-only"),
        ("next", "--all"),
        ("next", "--into", operating_video_store.scene),
        ("model",),
    ):
        completed = run_cli(cwd, *args, store=store)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["ok"] is True


def test_the_brief_names_the_scrum_and_waterfall_focus(
    operating_scrum_store: SimpleNamespace,
    operating_waterfall_store: SimpleNamespace,
) -> None:
    scrum = invoke("cto brief", {}, store=operating_scrum_store.store, actor="cto")["value"]
    waterfall = invoke(
        "cto brief", {}, store=operating_waterfall_store.store, actor="cto"
    )["value"]

    assert scrum["state"]["focus"]["state"] == "resolved"
    assert scrum["text"].startswith("Focus: Sprint one")
    assert waterfall["text"].startswith("Focus: Design")


def test_the_brief_says_project_wide_without_a_focus(tmp_path: Path) -> None:
    store = init_store(tmp_path / "nofocus", "scrum")
    _add(store, "sprint", "Not started")

    value = invoke("cto brief", {}, store=store, actor="cto")["value"]

    assert value["state"]["focus"]["state"] == "none"
    assert value["text"].startswith("Focus: none")


def test_next_all_still_respects_the_workflow(
    operating_scrum_store: SimpleNamespace,
) -> None:
    _move(operating_scrum_store.store, operating_scrum_store.story, "doing")
    _move(operating_scrum_store.store, operating_scrum_store.story, "done")

    value = invoke(
        "next", {"all": True}, store=operating_scrum_store.store, actor="cto"
    )["value"]

    assert operating_scrum_store.story not in value["queue"]


def test_the_worker_card_carries_the_focus_breadcrumb(
    operating_video_store: SimpleNamespace,
) -> None:
    from context_rail.context import surface
    from context_rail.gateway.gateway import Context
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack

    store = operating_video_store.store
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack("video", store),
        actor="cto",
    )
    card = surface.card(context, operating_video_store.shot)
    text = surface.render_card(card)

    assert "focus:" in text
    assert "actions:" in text
    assert "storyboard" in text
