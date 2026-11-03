"""``pack use`` maps workflow and status together when the vocabulary changes."""

from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag
from helpers import (
    init_pack_store,
    workflow_pack_document,
    write_pack,
)


def _switch(store: Path, name: str, mapping: list[str], key: str | None = None) -> dict:
    args = {
        "name": name,
        "expect": etag((store / "rail.yaml").read_bytes()),
        "confirm": f"USE PACK {name}",
        "map": mapping,
    }
    if key is not None:
        args["key"] = key
    return invoke("pack use", args, store=store, actor="human")


def test_an_ambiguous_status_names_the_item_and_its_workflow(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]

    refused = _switch(store, "flow", [])

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    items = refused["error"]["details"]["items"]
    assert {(row["slug"], row["workflow"]) for row in items} == {
        ("a-bug", "bugfix"),
        ("a-task", "sprint"),
    }


def test_an_explicit_map_carries_both_status_and_workflow(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]

    target = workflow_pack_document("single")
    target["workflows"] = {}
    target["transitions"] = {
        "todo": ["doing", "fixing", "done"],
        "doing": ["done"],
        "fixing": ["done"],
        "done": [],
    }
    target["presets"]["task"]["workflow"] = "default"
    target["presets"]["bug"]["workflow"] = "default"
    write_pack(store, "single", target)

    switched = _switch(
        store,
        "single",
        ["todo=todo", "doing=doing", "fixing=fixing"],
        key="twoways-to-single",
    )

    assert switched["ok"] is True, switched
    assert switched["value"]["from"] == "twoways"
    assert switched["value"]["to"] == "single"
    assert switched["value"]["workflow_changes"] == [
        {"slug": "a-bug", "from": "bugfix", "to": "default"},
        {"slug": "a-task", "from": "sprint", "to": "default"},
    ]
    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"], report
    assert report["value"]["issues"] == []

    replayed = _switch(
        store,
        "single",
        ["todo=todo", "doing=doing", "fixing=fixing"],
        key="twoways-to-single",
    )
    assert replayed["ok"] is True, replayed
    assert replayed["value"]["changes"] == switched["value"]["changes"]
    assert replayed["value"]["workflow_changes"] == switched["value"]["workflow_changes"]
    assert replayed["value"]["record"] == switched["value"]["record"]


def test_a_vanished_preset_refuses_the_switch(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]

    target = workflow_pack_document("plain")
    del target["presets"]["bug"]
    target["workflows"] = {}
    target["transitions"] = {
        "todo": ["doing", "fixing", "done"],
        "doing": ["done"],
        "fixing": ["done"],
        "done": [],
    }
    target["presets"]["task"]["workflow"] = "default"
    write_pack(store, "plain", target)

    refused = _switch(store, "plain", ["todo=todo", "doing=doing", "fixing=fixing"])

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert any(
        problem["field"] == "preset" and problem["value"] == "bug"
        for problem in refused["error"]["details"]["unsupported"]
    )


def test_a_workflow_qualified_map_splits_one_source_status(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "A bug"}, store=store)["ok"]

    target = workflow_pack_document("split")
    target["workflows"] = {
        "sprint": {
            "initial": "todo",
            "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
        },
        "bugfix": {
            "initial": "todo",
            "transitions": {"todo": ["fixing"], "fixing": ["done"], "done": []},
        },
    }
    write_pack(store, "split", target)

    switched = _switch(store, "split", ["sprint:todo=doing", "bugfix:todo=fixing"])

    assert switched["ok"] is True, switched
    assert switched["value"]["changes"] == [
        {"slug": "a-bug", "from": "todo", "to": "fixing"},
        {"slug": "a-task", "from": "todo", "to": "doing"},
    ]
    assert switched["value"]["status_map"] == {}
    assert invoke("show", {"slug": "a-task"}, store=store)["value"]["doc"]["status"] == "doing"
    assert invoke("show", {"slug": "a-bug"}, store=store)["value"]["doc"]["status"] == "fixing"


def test_a_status_outside_the_target_workflow_refuses(tmp_path: Path) -> None:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    shown = invoke("show", {"slug": "a-task"}, store=store)
    moved = invoke(
        "move",
        {
            "slug": "a-task",
            "status": "doing",
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor="cto",
    )
    assert moved["ok"], moved

    target = workflow_pack_document("narrow")
    target["workflows"] = {
        "sprint": {
            "initial": "todo",
            "transitions": {"todo": ["done"], "done": []},
        }
    }
    target["presets"]["task"]["workflow"] = "sprint"
    target["presets"]["bug"]["workflow"] = "sprint"
    write_pack(store, "narrow", target)

    refused = _switch(store, "narrow", ["todo=todo", "doing=doing", "fixing=fixing"])

    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert ["workflow_status", "a-task"] in refused["error"]["details"]["problems"]
