"""Per-status execution flags: a pack steers the work queue, not just moves."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.kernel import structure
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from helpers import approve_store, init_store, write_pack


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
    assert workflow.queued("todo")
    assert workflow.queued("doing")
    assert not workflow.queued("blocked")
    assert not workflow.queued("done")


@pytest.mark.parametrize("name", ["flow", "scrum"])
def test_shipped_packs_keep_an_item_in_review_out_of_the_queue(
    name: str, tmp_path: Path
) -> None:
    pack = load_pack(name, tmp_path)
    workflow = pack.workflow("default")
    assert workflow.queued("doing" if name == "scrum" else "building")
    assert not workflow.queued("review")


def test_an_unknown_execution_status_refuses(tmp_path: Path) -> None:
    document = _document("slim")
    document["execution"] = {"nope": {"queue": False}}
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
    document["execution"] = {"doing": {"queue": "no"}}
    write_pack(tmp_path, "slim", document)
    with pytest.raises(ModelError) as caught:
        load_pack("slim", tmp_path)
    assert caught.value.code == "VALIDATION_FAILED"


def test_the_kernel_skips_a_row_the_pack_marks_unqueued() -> None:
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
            queued=False,
        ),
    ]
    assert structure.executable(rows) == ["open"]


def test_status_leaves_a_unqueued_item_out(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _document("basic")
    document["execution"] = {"doing": {"queue": False}}
    write_pack(store, "basic", document)
    approve_store(store)

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
