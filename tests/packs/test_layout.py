"""A pack's optional layout.yaml seeds a new store's rail.yaml."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.model.errors import ModelError
from context_rail.model.manifest import DEFAULT_MANIFEST, initial_manifest, load_manifest
from context_rail.model.pack import pack_layout
from helpers import init_store

PACKS = ("basic", "flow", "scrum", "waterfall")


def _pack_document(name: str) -> dict:
    text = (
        resources.files("context_rail")
        .joinpath("packs", name, "pack.yaml")
        .read_text(encoding="utf-8")
    )
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data


def _write_local_pack(
    store: Path, name: str, *, layout: dict | None = None
) -> Path:
    root = store / "packs" / name
    root.mkdir(parents=True, exist_ok=True)
    document = _pack_document("basic")
    document["name"] = name
    (root / "pack.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    if layout is not None:
        (root / "layout.yaml").write_text(
            yaml.safe_dump(layout, sort_keys=False), encoding="utf-8"
        )
    return root


def _copy_builtin_pack(store: Path, name: str) -> Path:
    source = Path(str(resources.files("context_rail") / "packs" / name))
    root = store / "packs" / name
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, root)
    return root


@pytest.mark.parametrize("name", PACKS)
def test_builtin_packs_ship_layout_defaults(name: str) -> None:
    layout = pack_layout(name, Path("."))
    assert layout is not None
    assert layout["placement"] == DEFAULT_MANIFEST["placement"]


def test_init_uses_the_pack_layout_and_pins_its_folders(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(
        store, "custom", layout={"placement": {"item": "work/{preset}/{slug}.md"}}
    )

    result = invoke(
        "init", {"dir": str(tmp_path), "pack": "custom", "scaffold": True}, actor="human"
    )

    assert result["ok"] is True, result
    manifest = load_manifest(store)
    assert manifest.placement["item"] == "work/{preset}/{slug}.md"
    assert manifest.placement["note"] == DEFAULT_MANIFEST["placement"]["note"]
    assert (store / "work").is_dir()
    assert not (store / "items").exists()


def test_a_pack_without_a_layout_keeps_the_default_manifest(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(store, "custom")

    manifest, data = initial_manifest("custom", store)

    assert data == {**DEFAULT_MANIFEST, "pack": "custom"}
    assert manifest.placement == DEFAULT_MANIFEST["placement"]


def test_pack_layout_can_override_one_preset(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(
        store, "custom", layout={"presets": {"task": "backlog/{slug}.md"}}
    )

    manifest, _data = initial_manifest("custom", store)

    assert manifest.preset_placement["task"] == "backlog/{slug}.md"
    assert manifest.placement["item"] == DEFAULT_MANIFEST["placement"]["item"]


def test_pack_layout_uses_the_manifest_fail_closed_rules(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(
        store,
        "custom",
        layout={"placement": {"item": "items/{bogus}/{slug}.md"}},
    )

    with pytest.raises(ModelError) as error:
        initial_manifest("custom", store)

    assert error.value.code == "VALIDATION_FAILED"
    assert "bogus" in error.value.message


def test_pack_layout_refuses_an_unknown_key(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(store, "custom", layout={"schema": 1})

    with pytest.raises(ModelError) as error:
        initial_manifest("custom", store)

    assert error.value.code == "VALIDATION_FAILED"
    assert "unknown key" in error.value.message


def test_pack_layout_refuses_an_undeclared_preset(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    _write_local_pack(
        store, "custom", layout={"presets": {"bogus": "bogus/{slug}.md"}}
    )

    with pytest.raises(ModelError) as error:
        initial_manifest("custom", store)

    assert error.value.code == "VALIDATION_FAILED"
    assert "bogus" in error.value.message


def test_pack_validate_reports_an_invalid_layout(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    _write_local_pack(
        store,
        "bad",
        layout={"placement": {"item": "items/{bogus}/{slug}.md"}},
    )

    result = invoke("pack validate", {"name": "bad"}, store=store, actor="human")

    assert result["ok"] is True
    assert result["value"]["valid"] is False
    assert any(
        problem["path"] == "layout.yaml"
        for problem in result["value"]["problems"]
    )


def test_pack_create_copies_the_layout(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    result = invoke(
        "pack create", {"name": "mine", "from": "basic"}, store=store, actor="cto"
    )

    assert result["ok"] is True, result
    assert (store / "packs" / "mine" / "layout.yaml").is_file()


def test_onboarding_uses_the_pack_layout(
    tmp_path: Path, answers: Path
) -> None:
    store = tmp_path / "context-rail"
    root = _copy_builtin_pack(store, "basic")
    (root / "layout.yaml").write_text(
        yaml.safe_dump(
            {"placement": {"item": "work/{preset}/{slug}.md"}}, sort_keys=False
        ),
        encoding="utf-8",
    )

    result = invoke(
        "init",
        {
            "dir": str(tmp_path),
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )

    assert result["ok"] is True, result
    assert load_manifest(store).placement["item"] == "work/{preset}/{slug}.md"
    assert (store / "work").is_dir()


def test_migrate_uses_the_pack_layout(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "target"
    store = target_dir / "context-rail"
    root = _copy_builtin_pack(store, "basic")
    (root / "layout.yaml").write_text(
        yaml.safe_dump(
            {"placement": {"item": "work/{preset}/{slug}.md"}}, sort_keys=False
        ),
        encoding="utf-8",
    )

    result = invoke(
        "migrate",
        {"source": str(legacy_v2_store), "dir": str(target_dir)},
        actor="human",
    )

    assert result["ok"] is True, result
    assert load_manifest(store).placement["item"] == "work/{preset}/{slug}.md"
    assert (store / "work" / "task" / "login.md").is_file()
