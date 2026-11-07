from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import init_store, write_pack

from context_rail.gateway.gateway import Context, invoke
from context_rail.model.codegraph import (
    CONFIG_CHOICES,
    code_graph_source,
    effective_code_graph,
)
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.approval import _manifest_hash


def _set_manifest_key(store: Path, value: Any) -> None:
    path = store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _resolved(store: Path, pack: str) -> tuple[str, str]:
    manifest = load_manifest(store)
    default = load_pack(pack, store).policy.get("code_graph")
    return (
        effective_code_graph(manifest.code_graph, default),
        code_graph_source(manifest.code_graph, default),
    )


def test_a_code_pack_defaults_to_builtin(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    assert load_manifest(store).code_graph is None
    assert _resolved(store, "basic") == ("builtin", "default")


def test_the_video_pack_defaults_to_none(tmp_path: Path) -> None:
    store = init_store(tmp_path, "video")
    assert _resolved(store, "video") == ("none", "pack")

    _set_manifest_key(store, "builtin")

    assert _resolved(store, "video") == ("builtin", "rail.yaml")


@pytest.mark.parametrize(
    ("configured", "pack_default", "expected", "source"),
    [
        ("none", None, "none", "rail.yaml"),
        ("builtin", "none", "builtin", "rail.yaml"),
        ("none", "builtin", "none", "rail.yaml"),
        ("graphify", "none", "graphify", "rail.yaml"),
        (None, "none", "none", "pack"),
        (None, "builtin", "builtin", "pack"),
        (None, None, "builtin", "default"),
    ],
)
def test_rail_yaml_wins_over_the_pack(
    configured: str | None, pack_default: str | None, expected: str, source: str
) -> None:
    assert effective_code_graph(configured, pack_default) == expected
    assert code_graph_source(configured, pack_default) == source


@pytest.mark.parametrize("value", ["graphify", "gitnexus", "sourcegraph", True, 1])
def test_a_pack_policy_value_outside_builtin_or_none_is_refused(
    tmp_path: Path, value: Any
) -> None:
    store = init_store(tmp_path)
    document = _pack_document("video")
    document["name"] = "custom"
    document["policy"]["code_graph"] = value
    write_pack(store, "custom", document)

    with pytest.raises(ModelError) as caught:
        load_pack("custom", store)

    assert "code_graph" in caught.value.message


def test_a_child_pack_inherits_the_base_default(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _pack_document("video")
    document["name"] = "child"
    document["extends"] = "video"
    document["policy"].pop("code_graph")
    write_pack(store, "child", document)

    assert load_pack("child", store).policy["code_graph"] == "none"


def _pack_document(name: str) -> dict[str, Any]:
    from importlib import resources

    text = resources.files("context_rail").joinpath("packs", name, "pack.yaml").read_text(
        encoding="utf-8"
    )
    return yaml.safe_load(text)


@pytest.mark.parametrize("choice", CONFIG_CHOICES)
def test_each_choice_is_accepted(tmp_path: Path, choice: str) -> None:
    store = init_store(tmp_path)
    _set_manifest_key(store, choice)

    assert load_manifest(store).code_graph == choice


@pytest.mark.parametrize("value", ["sourcegraph", "", 3, True])
def test_an_unknown_choice_is_refused(tmp_path: Path, value: Any) -> None:
    store = init_store(tmp_path)
    _set_manifest_key(store, value)

    with pytest.raises(ModelError) as caught:
        load_manifest(store)

    assert caught.value.code == "VALIDATION_FAILED"
    assert caught.value.details["field"] == "code_graph"


def test_the_key_is_part_of_the_approval_on_both_paths(approved_store: Path) -> None:
    before = invoke("show", {"slug": "charter"}, store=approved_store, actor="human")
    assert before["value"]["approval"]["state"] == "approved"

    _set_manifest_key(approved_store, "none")

    after = invoke("show", {"slug": "charter"}, store=approved_store, actor="human")
    assert after["value"]["approval"]["state"] == "mismatch"

    context = Context(
        store=approved_store,
        manifest=load_manifest(approved_store),
        pack=load_pack("basic", approved_store),
        actor="human",
    )
    try:
        with_key = _manifest_hash(context, "scrum")
        context.manifest = _without_key(context.manifest)
        without_key = _manifest_hash(context, "scrum")
    finally:
        context.close()
    assert with_key != without_key


def _without_key(manifest: Any) -> Any:
    from dataclasses import replace

    return replace(manifest, code_graph=None)


def test_absent_key_keeps_an_existing_approval(approved_store: Path) -> None:
    shown = invoke("show", {"slug": "charter"}, store=approved_store, actor="human")

    assert "code_graph" not in (approved_store / "rail.yaml").read_text(encoding="utf-8")
    assert shown["value"]["approval"]["state"] == "approved"
