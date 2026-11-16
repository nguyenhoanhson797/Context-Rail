"""``pack use`` maps workflow and status together when the vocabulary changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import (
    approve_picture,
    init_pack_store,
    workflow_pack_document,
    write_pack,
)

from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import initial_manifest
from context_rail.model.pack import load_pack
from context_rail.store.doc import etag
from context_rail.store.migration import migration_needs_graph


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


@pytest.mark.usefixtures("named_human")
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


@pytest.mark.usefixtures("named_human")
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
    # A pack switch changes the protected set, so the human approves the new picture.
    approved = approve_picture(store)
    assert approved["ok"], approved
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


@pytest.mark.usefixtures("named_human")
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


@pytest.mark.usefixtures("named_human")
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
    assert [
        {k: row[k] for k in ("slug", "from", "to")}
        for row in switched["value"]["changes"]
    ] == [
        {"slug": "a-bug", "from": "todo", "to": "fixing"},
        {"slug": "a-task", "from": "todo", "to": "doing"},
    ]
    assert switched["value"]["status_map"] == {}
    assert invoke("show", {"slug": "a-task"}, store=store)["value"]["doc"]["status"] == "doing"
    assert invoke("show", {"slug": "a-bug"}, store=store)["value"]["doc"]["status"] == "fixing"


@pytest.mark.usefixtures("named_human")
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


@pytest.mark.parametrize(
    "pack_name",
    ["basic", "flow", "scrum", "waterfall", "video", "portfolio"],
)
def test_migration_succeeds_across_all_six_shipped_layouts_for_v1_and_v2(
    pack_name: str,
    legacy_v1_store: Path,
    legacy_v2_store: Path,
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_name, tmp_path / "none")
    manifest, _ = initial_manifest(pack_name, tmp_path / "none")
    assert migration_needs_graph(manifest, pack) is False

    for label, source in (("v1", legacy_v1_store), ("v2", legacy_v2_store)):
        dry_dir = tmp_path / f"{pack_name}-{label}-dry"
        dry = invoke(
            "migrate",
            {
                "source": str(source),
                "dir": str(dry_dir),
                "pack": pack_name,
                "dry_run": True,
            },
            actor="human",
        )
        assert dry["ok"] is True, dry
        assert dry["value"]["pack"] == pack_name
        assert dry["value"]["written"] == []
        assert not dry_dir.exists()

        target_dir = tmp_path / f"{pack_name}-{label}-real"
        real = invoke(
            "migrate",
            {
                "source": str(source),
                "dir": str(target_dir),
                "pack": pack_name,
            },
            actor="human",
        )
        assert real["ok"] is True, real
        store = target_dir / "context-rail"
        assert (store / "rail.yaml").is_file()
        if label == "v2":
            assert (store / "records/2026-01/01JLEGACYEVENT00000000001.json").is_file()
            assert (store / "records/2026-01/01JLEGACYEVENT00000000002.json").is_file()
        if pack_name == "portfolio":
            from context_rail.gateway.execution import human_channel

            with human_channel():
                report = invoke("doctor", {}, store=store)
        else:
            report = invoke("doctor", {}, store=store, actor="human")
        assert report["ok"] is True, report
        codes = {issue["code"] for issue in report["value"]["issues"]}
        assert "parse_error" not in codes
        assert "record_tampered" not in codes
        assert "gate_bypassed" not in codes


def test_migration_needs_graph_refuses_structural_protocol_record_but_ignores_unused_result_artifacts(
    tmp_path: Path,
) -> None:
    pack = load_pack("basic", tmp_path / "none")
    manifest, data = initial_manifest("basic", tmp_path / "none")
    assert migration_needs_graph(manifest, pack) is False

    # Structural event placement (e.g. {kind} token, which is structural) must be refused.
    event_data = {
        **data,
        "presets": {**data["presets"], "event": "records/{kind}/{id}.json"},
    }
    from context_rail.model.manifest import manifest_from_mapping

    event_manifest = manifest_from_mapping(event_data, tmp_path / "none")
    assert migration_needs_graph(event_manifest, pack) is True

    # Structural placement.record (used by migration.completed / protocol records) must be refused.
    rec_data = {
        **data,
        "placement": {**data["placement"], "record": "records/{kind}/{id}.json"},
    }
    rec_manifest = manifest_from_mapping(rec_data, tmp_path / "none")
    assert migration_needs_graph(rec_manifest, pack) is True

    # Structural item/note placements (both primitive and non-artifact preset) must be refused.
    item_data = {
        **data,
        "placement": {**data["placement"], "item": "items/{part_of.slug}/{slug}.md"},
    }
    assert migration_needs_graph(manifest_from_mapping(item_data, tmp_path / "none"), pack) is True
    note_data = {
        **data,
        "presets": {**data["presets"], "vision": "project/{part_of.slug}/{slug}.md"},
    }
    assert migration_needs_graph(manifest_from_mapping(note_data, tmp_path / "none"), pack) is True

    # Unused declared result/review artifact placements with {about.slug} must NOT trigger refusal.
    assert migration_needs_graph(manifest, pack) is False
    # Even a custom workflow-only result preset with {about.slug} is ignored by migration.
    wf_only = {
        **data,
        "presets": {
            **data["presets"],
            "result": "records/results/{field:workflow_name}/{about.slug}/{id}.md",
            "review-result": "records/result-reviews/{field:workflow_name}/{about.slug}/{id}.md",
        },
    }
    assert migration_needs_graph(manifest_from_mapping(wf_only, tmp_path / "none"), pack) is False

