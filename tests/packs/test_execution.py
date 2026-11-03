"""Per-status execution flags: a pack steers dispatch and merge, not just moves."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.kernel import structure
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from helpers import init_store, write_pack


def _document(name: str) -> dict:
    text = (
        resources.files("context_rail")
        .joinpath("packs", "basic", "pack.yaml")
        .read_text(encoding="utf-8")
    )
    data = yaml.safe_load(text)
    data["name"] = name
    return data


def test_statuses_without_flags_keep_the_category_defaults(tmp_path: Path) -> None:
    pack = load_pack("basic", tmp_path)
    workflow = pack.workflow("default")
    assert workflow.dispatchable("todo")
    assert workflow.dispatchable("doing")
    assert not workflow.dispatchable("blocked")
    assert not workflow.dispatchable("done")
    assert workflow.mergeable("doing")
    assert workflow.mergeable("todo")
    assert not workflow.mergeable("done")
    assert not workflow.mergeable("dropped")


@pytest.mark.parametrize("name", ["flow", "scrum"])
def test_shipped_packs_stop_dispatching_an_item_in_review(
    name: str, tmp_path: Path
) -> None:
    pack = load_pack(name, tmp_path)
    workflow = pack.workflow("default")
    assert workflow.dispatchable("doing" if name == "scrum" else "building")
    assert not workflow.dispatchable("review")
    assert workflow.mergeable("review")


def test_an_unknown_execution_status_refuses(tmp_path: Path) -> None:
    document = _document("slim")
    document["execution"] = {"nope": {"dispatch": False}}
    write_pack(tmp_path, "slim", document)
    with pytest.raises(ModelError) as caught:
        load_pack("slim", tmp_path)
    assert caught.value.code == "VALIDATION_FAILED"


def test_an_unknown_execution_flag_refuses(tmp_path: Path) -> None:
    document = _document("slim")
    document["execution"] = {"doing": {"deploy": False}}
    write_pack(tmp_path, "slim", document)
    with pytest.raises(ModelError) as caught:
        load_pack("slim", tmp_path)
    assert caught.value.code == "VALIDATION_FAILED"


def test_a_non_boolean_flag_refuses(tmp_path: Path) -> None:
    document = _document("slim")
    document["execution"] = {"doing": {"dispatch": "no"}}
    write_pack(tmp_path, "slim", document)
    with pytest.raises(ModelError) as caught:
        load_pack("slim", tmp_path)
    assert caught.value.code == "VALIDATION_FAILED"


def test_the_kernel_skips_a_row_the_pack_marks_non_dispatchable() -> None:
    rows = [
        structure.ItemRow(
            slug="open",
            category="active",
            part_of=(),
            depends_on=(),
            last_activity_ms=None,
        ),
        structure.ItemRow(
            slug="held",
            category="active",
            part_of=(),
            depends_on=(),
            last_activity_ms=None,
            dispatchable=False,
        ),
    ]
    assert structure.executable(rows) == ["open"]


def test_status_leaves_a_non_dispatchable_item_out(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _document("basic")
    document["execution"] = {"doing": {"dispatch": False}}
    write_pack(store, "basic", document)

    created = invoke("add", {"preset": "task", "title": "Task"}, store=store, actor="cto")[
        "value"
    ]
    moved = invoke(
        "move",
        {"slug": created["slug"], "status": "doing", "expect": created["etag"]},
        store=store,
        actor="cto",
    )
    assert moved["ok"] is True, moved

    value = invoke("status", {}, store=store, actor="cto")["value"]

    assert created["slug"] not in value["executable"]
    assert value["counts"]["active"] == 1
