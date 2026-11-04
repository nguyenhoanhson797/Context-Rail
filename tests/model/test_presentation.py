"""The presentation contract: ``surface.yaml`` is separate from the pack."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from context_rail.model.presentation import (
    LABEL_KEYS,
    SCREEN_KINDS,
    default_presentation,
    load_presentation,
    presentation_hash,
)
from helpers import write_pack

_DOCUMENT = {
    "name": "studio",
    "version": 2,
    "statuses": {
        "draft": "not_started",
        "active": "active",
        "done": "done",
        "dropped": "dropped",
    },
    "initial": "draft",
    "transitions": {
        "draft": ["active", "dropped"],
        "active": ["done", "dropped"],
        "done": ["active"],
        "dropped": ["draft"],
    },
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "scene": {
            "primitive": "item",
            "tags": ["scene"],
            "container": True,
            "fields": {"sequence": ["one", "two", "three"]},
        },
        "shot": {
            "primitive": "item",
            "tags": ["shot"],
            "container": False,
            "fields": {"model": ["a", "b"], "duration": ["short", "long"]},
        },
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": ["decision"]},
        "event": {"primitive": "record", "tags": ["event"]},
        "check": {"primitive": "check", "tags": []},
        "charter": {"primitive": "policy", "tags": []},
        "vision": {"primitive": "note", "tags": []},
        "wont": {"primitive": "note", "tags": []},
        "skill": {"primitive": "note", "tags": []},
        "cto": {"primitive": "actor", "tags": []},
        "profile": {"primitive": "actor", "tags": []},
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
    "operating": {
        "version": 1,
        "unit": "shot",
        "hierarchy": [{"preset": "scene", "order": "sequence", "required": True}],
        "focus": {"preset": "scene", "category": "active", "resolve": "unique"},
        "queue": {"presets": ["shot"]},
        "progress": {"group": "scene", "of": ["shot"], "complete": {"category": "done"}},
    },
}

_SURFACE = {
    "version": 1,
    "labels": {
        "shot": {"one": "Shot", "many": "Shots"},
        "scene": {"one": "Scene", "many": "Scenes"},
    },
    "screens": [
        {"id": "shots", "kind": "board", "title": "Shots"},
        {"id": "scenes", "kind": "plan", "title": "Scenes"},
        {"id": "assets", "kind": "catalog", "title": "Assets"},
    ],
    "fields": {
        "card": ["model", "duration", "status"],
        "filters": ["scene", "model", "status"],
        "columns": ["title", "model", "duration"],
    },
}


def _write_surface(
    store: Path, payload: dict, *, name: str = "studio"
) -> Path:
    path = store / "packs" / name / "surface.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return path


def _pack(tmp_path: Path) -> object:
    write_pack(tmp_path, "studio", _DOCUMENT)
    return load_pack("studio", tmp_path)


def test_a_missing_surface_file_yields_neutral_defaults(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    presentation = load_presentation("studio", tmp_path, pack)

    assert presentation.labels["shot"].one == "Shot"
    assert presentation.labels["shot"].many == "Shots"
    assert presentation.labels["scene"].many == "Scenes"
    assert presentation.labels["scope"].many == "Scopes"
    assert [screen.kind for screen in presentation.screens][:3] == [
        "summary",
        "board",
        "plan",
    ]
    assert presentation.fields["card"] == ()


def test_default_presentation_is_built_from_the_pack(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    presentation = default_presentation(pack)

    assert set(presentation.labels) <= set(pack.presets)
    assert presentation.version == 1
    assert all(screen.kind in SCREEN_KINDS for screen in presentation.screens)


def test_a_declared_surface_file_loads(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    _write_surface(tmp_path, _SURFACE)

    presentation = load_presentation("studio", tmp_path, pack)

    assert presentation.labels["shot"].many == "Shots"
    assert [screen.id for screen in presentation.screens] == [
        "shots",
        "scenes",
        "assets",
    ]
    assert presentation.fields["card"] == ("model", "duration", "status")
    assert presentation.fields["filters"] == ("scene", "model", "status")


def test_a_label_change_changes_only_the_presentation_hash(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    _write_surface(tmp_path, _SURFACE)
    before = load_presentation("studio", tmp_path, pack)

    changed = {**_SURFACE, "labels": {**_SURFACE["labels"], "shot": {"one": "Take", "many": "Takes"}}}
    _write_surface(tmp_path, changed)
    after = load_presentation("studio", tmp_path, pack)

    assert presentation_hash(before) != presentation_hash(after)


def test_the_presentation_hash_is_canonical(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    _write_surface(tmp_path, _SURFACE)
    first = load_presentation("studio", tmp_path, pack)
    reordered = {
        key: _SURFACE[key]
        for key in ("fields", "screens", "labels", "version")
    }
    _write_surface(tmp_path, reordered)

    assert presentation_hash(load_presentation("studio", tmp_path, pack)) == (
        presentation_hash(first)
    )


def test_the_built_in_pack_is_the_fallback_surface(tmp_path: Path) -> None:
    pack = load_pack("scrum", tmp_path)
    presentation = load_presentation("scrum", tmp_path, pack)

    assert presentation.labels["story"].one == "Story"


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        (("version", 2), "version"),
        (("mood", "dark"), "unknown"),
        (("labels.ghost", {"one": "Ghost", "many": "Ghosts"}), "ghost"),
        (("labels.shot", {"one": "Shot"}), "many"),
        (("screens", [{"id": "s", "kind": "gallery", "title": "S"}]), "kind"),
        (("screens", [{"id": "s", "kind": "board"}]), "title"),
        (
            (
                "screens",
                [
                    {"id": "s", "kind": "board", "title": "One"},
                    {"id": "s", "kind": "plan", "title": "Two"},
                ],
            ),
            "repeat",
        ),
        (("fields.card", ["ghost"]), "ghost"),
        (("fields.columns", ["no_such_field"]), "no_such_field"),
        (("fields.rows", ["title"]), "rows"),
    ],
)
def test_an_invalid_surface_is_refused(
    tmp_path: Path, mutation: tuple[str, object], path: str
) -> None:
    pack = _pack(tmp_path)
    payload = yaml.safe_load(yaml.safe_dump(_SURFACE))
    key, value = mutation
    if key == "labels.ghost":
        payload["labels"]["ghost"] = value
    elif key == "labels.shot":
        payload["labels"]["shot"] = value
    elif key == "fields.card":
        payload["fields"]["card"] = value
    elif key == "fields.columns":
        payload["fields"]["columns"] = value
    elif key == "fields.rows":
        payload["fields"]["rows"] = value
    else:
        payload[key] = value
    _write_surface(tmp_path, payload)

    with pytest.raises(ModelError) as caught:
        load_presentation("studio", tmp_path, pack)

    assert caught.value.code == "VALIDATION_FAILED"
    assert path in caught.value.message


def test_label_keys_are_the_only_label_keys(tmp_path: Path) -> None:
    assert LABEL_KEYS == ("one", "many")
