"""Upgrade and history-preservation tests for result artifact adoption."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from helpers import (
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    approve_picture,
    init_pack_store,
    record_inventory,
    record_result_for_test,
    records_of,
    result_pack_document,
    set_preset_placement,
    tree_bytes,
    write_pack,
    write_review_notes_for_test,
)

from context_rail.core.result import is_ok
from context_rail.doctor.checks import gate_report, run_doctor
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store import query
from context_rail.store.doc import etag
from context_rail.store.results import historical_result_exempt

PHRASE = "UPGRADE store"


def _upgrade(store: Path, **args: Any) -> dict[str, Any]:
    return invoke("upgrade", args, store=store, actor="human")


def _move(store: Path, slug: str, status: str, *, actor: str = "cto") -> dict[str, Any]:
    shown = invoke("show", {"slug": slug}, store=store, actor=actor)
    assert is_ok(shown), shown
    return invoke(
        "move",
        {"slug": slug, "status": status, "expect": shown["value"]["doc"]["etag"]},
        store=store,
        actor=actor,
    )


def _strip_shipped_result_layout_and_copy_old_pack(
    store: Path,
    *,
    pack_name: str = "basic",
    drop_presets: tuple[str, ...] = ("result", "review-result"),
) -> None:
    manifest_path = store / "rail.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    for key in drop_presets:
        data["presets"].pop(key, None)
    manifest_path.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )


def test_upgrade_dry_run_and_confirm_adopt_missing_result_artifacts_for_shipped_pack(
    approved_store: Path,
) -> None:
    _strip_shipped_result_layout_and_copy_old_pack(approved_store)
    # The store starts approved with the full layout; stripping the result
    # placements simulates an older manifest before result artifacts were added
    # (so before upgrade, approval already mismatches the original charter, and
    # after upgrade, rail.yaml matches the original approved snapshot? No: let's
    # assert rail.yaml now holds the shipped result placements, and verify on a
    # local pack store that started without result placements that upgrade leaves
    # approval mismatched until human reapproves).
    before = tree_bytes(approved_store)

    dry = _upgrade(approved_store, dry_run=True)

    assert is_ok(dry), dry
    assert tree_bytes(approved_store) == before
    shipped = dry["value"]["shipped"]
    assert shipped is not None
    assert "rail.yaml" in shipped["seeded"] or "rail.yaml" in shipped["adopted"]
    assert set(shipped["placements"]["added"]) == {"result", "review-result"}
    assert "packs/basic/templates/result.md" in shipped["seeded"]
    assert "packs/basic/templates/review-result.md" in shipped["seeded"]
    assert any("approve charter" in step for step in dry["value"]["next"])

    applied = _upgrade(approved_store, confirm=PHRASE)

    assert is_ok(applied), applied
    assert (approved_store / "packs/basic/templates/result.md").is_file()
    assert (approved_store / "packs/basic/templates/review-result.md").is_file()
    manifest = load_manifest(approved_store)
    assert manifest.preset_placement["result"] == (
        "records/results/{field:workflow_name}/{about.slug}/{id}.md"
    )
    assert manifest.preset_placement["review-result"] == (
        "records/result-reviews/{field:workflow_name}/{about.slug}/{id}.md"
    )

    # Second upgrade is idempotent and changes nothing.
    settled = tree_bytes(approved_store)
    again = _upgrade(approved_store, confirm=PHRASE)
    assert is_ok(again), again
    assert again["value"]["changed"] == []
    assert again["value"]["shipped"]["adopted"] == []
    assert again["value"]["shipped"]["seeded"] == []
    assert again["value"]["shipped"].get("placements", {}).get("added", []) == []
    assert tree_bytes(approved_store) == settled


def test_upgrade_preserves_custom_guidance_templates_placements_and_old_record_paths(
    approved_store: Path,
) -> None:
    # Add pre-existing records (a decision Markdown record and a legacy event).
    added = invoke("add", {"preset": "task", "title": "Early task"}, store=approved_store, actor="cto")
    assert is_ok(added), added
    task = added["value"]["slug"]
    assert is_ok(_move(approved_store, task, "doing"))
    res = record_result_for_test(approved_store, task, actor="implementer")
    decision = invoke(
        "add",
        {"preset": "decision", "title": "Use local templates"},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(decision), decision
    before_inventory = record_inventory(approved_store)
    assert res["record"] in before_inventory

    # Customize profile, a template (with incompatible headings), and custom placement.
    custom_profile = approved_store / "workspace/profiles/implementer/profile.md"
    custom_profile.write_text(
        custom_profile.read_text(encoding="utf-8").rstrip("\n") + "\n- Keep our rule.\n",
        encoding="utf-8",
        newline="\n",
    )
    original_profile_bytes = custom_profile.read_bytes()

    custom_tpl = approved_store / "packs/basic/templates/result.md"
    custom_tpl.parent.mkdir(parents=True, exist_ok=True)
    custom_tpl.write_text("# {title}\n\n## CustomOnly\n", encoding="utf-8", newline="\n")
    original_tpl_bytes = custom_tpl.read_bytes()

    custom_placement = "records/custom-results/{field:workflow_name}/{about.slug}/{id}.md"
    set_preset_placement(approved_store, result=custom_placement)
    # Remove review-result placement to verify absent placement still gets seeded while custom stays.
    _strip_shipped_result_layout_and_copy_old_pack(approved_store, drop_presets=("review-result",))

    upgraded = _upgrade(approved_store, confirm=PHRASE)

    assert is_ok(upgraded), upgraded
    assert custom_profile.read_bytes() == original_profile_bytes
    assert custom_tpl.read_bytes() == original_tpl_bytes
    manifest = load_manifest(approved_store)
    assert manifest.preset_placement["result"] == custom_placement
    assert (
        manifest.preset_placement["review-result"]
        == "records/result-reviews/{field:workflow_name}/{about.slug}/{id}.md"
    )
    assert (approved_store / "packs/basic/templates/review-result.md").is_file()

    shipped = upgraded["value"]["shipped"]
    assert any(row["path"] == "workspace/profiles/implementer/profile.md" for row in shipped["customized"])
    assert any(row["path"] == "packs/basic/templates/result.md" for row in shipped["customized"])
    assert any(row["path"] == "rail.yaml:presets.result" for row in shipped["customized"])
    assert any(
        "packs/basic/templates/result.md" in hint["path"]
        and "Summary" in hint["message"]
        for hint in shipped["repair_hints"]
    )

    after_inventory = record_inventory(approved_store)
    assert all(after_inventory[key] == value for key, value in before_inventory.items())


def test_upgrade_reports_incompatible_custom_placement_with_repair_hint_without_overwriting(
    tmp_path: Path,
) -> None:
    doc = result_pack_document("badplace")
    store = init_pack_store(
        tmp_path,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=True,
    )
    bad_placement = "records/findings/{slug}.md"
    set_preset_placement(store, finding=bad_placement)
    manifest_before = (store / "rail.yaml").read_bytes()

    dry = _upgrade(store, dry_run=True)

    assert is_ok(dry), dry
    assert (store / "rail.yaml").read_bytes() == manifest_before
    shipped = dry["value"]["shipped"]
    assert shipped is not None
    assert any(
        "presets.finding" in hint["path"] and "{id}" in hint["message"]
        for hint in shipped["repair_hints"]
    )

    applied = _upgrade(store, confirm=PHRASE)
    assert is_ok(applied), applied
    raw_manifest = yaml.safe_load((store / "rail.yaml").read_text(encoding="utf-8"))
    assert raw_manifest["presets"]["finding"] == bad_placement


def test_upgrade_never_injects_literal_result_preset_into_custom_contractless_pack(
    tmp_path: Path,
) -> None:
    custom = {
        "name": "custom-legacy",
        "version": 1,
        "statuses": {"todo": "not_started", "doing": "active", "done": "done"},
        "initial": "todo",
        "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
        "presets": {
            "goal": {"primitive": "item", "tags": ["goal"], "container": True},
            "scope": {"primitive": "item", "tags": ["scope"], "container": True},
            "task": {"primitive": "item", "tags": ["task"], "container": False},
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
        },
        "policy": {"stuck_hours": 24},
    }
    store = init_pack_store(tmp_path, custom, approve=True)
    before_pack = (store / "packs/custom-legacy/pack.yaml").read_bytes()
    before_manifest = (store / "rail.yaml").read_bytes()

    result = _upgrade(store, confirm=PHRASE)

    assert is_ok(result), result
    assert (store / "packs/custom-legacy/pack.yaml").read_bytes() == before_pack
    assert (store / "rail.yaml").read_bytes() == before_manifest
    pack = load_pack("custom-legacy", store)
    assert pack.results is None
    assert "result" not in pack.presets
    assert "result" not in load_manifest(store).preset_placement


def test_reopened_adopted_history_requires_a_new_result_and_reapproval_keeps_requirements(
    tmp_path: Path,
) -> None:
    legacy_doc = result_pack_document("hist")
    legacy_doc.pop("results", None)
    legacy_doc["workflows"]["bugfix"].pop("results", None)
    store = init_pack_store(tmp_path, legacy_doc, approve=True)

    done_task = invoke("add", {"preset": "task", "title": "Done before contract"}, store=store, actor="cto")["value"]["slug"]
    assert is_ok(_move(store, done_task, "doing"))
    assert is_ok(_move(store, done_task, "done"))

    # Add contract to pack without templates/placements yet, then adopt via human upgrade.
    contract_doc = result_pack_document("hist")
    contract_doc["transitions"] = {"todo": ["doing"], "doing": ["done"], "done": ["todo"]}
    contract_doc["workflows"]["sprint"]["transitions"]["done"] = ["todo"]
    write_pack(store, "hist", contract_doc)
    # Provide pack layout defaults like a pack update does, while store rail.yaml still lacks them.
    (store / "packs/hist/layout.yaml").write_text(
        yaml.safe_dump(
            {
                "presets": {
                    "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
                    "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    # Seed templates via local pack resources? Keep templates absent so upgrade seeds from sibling or local?
    # Write missing templates under packs/hist/templates only? No: test layout seeding + custom local pack.
    tpl_dir = store / "packs/hist/templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (tpl_dir / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )

    upgraded = _upgrade(store, confirm=PHRASE)
    assert is_ok(upgraded), upgraded
    assert set(upgraded["value"]["shipped"]["placements"]["added"]) == {"finding", "assessment"}
    assert invoke("show", {"slug": "charter"}, store=store, actor="human")["value"]["approval"]["state"] == "mismatch"

    # First human approval creates only adoption facts; no fabricated results.
    assert is_ok(approve_picture(store, actor="human"))
    manifest = load_manifest(store)
    pack = load_pack("hist", store)
    ctx = Context(store=store, manifest=manifest, pack=pack, actor="human")
    try:
        ctx.index.sync()
        assert query.result_count(ctx.index, done_task) == 0
        assert historical_result_exempt(ctx, done_task) is True
        adoptions = records_of(store, "results.adopted")
        assert len(adoptions) == 1
        assert [row["item"] for row in adoptions[0]["data"]["historical_done"]] == [done_task]
        report = gate_report(store, manifest, pack, ctx.index)
        assert next(r for r in report if r["item"] == done_task)["allowed"] is True
        doc_codes = {i["code"] for i in run_doctor(store, manifest, pack, ctx.index)["issues"]}
        assert "gate_bypassed" not in doc_codes
    finally:
        ctx.close()

    # Reapproval never clears contract requirements or refreshes adoption for newly added items.
    second_done = invoke("add", {"preset": "task", "title": "Bypassed done"}, store=store, actor="cto")["value"]["slug"]
    second_path = store / "items/task" / f"{second_done}.md"
    second_path.write_text(
        second_path.read_text(encoding="utf-8").replace("status: todo", "status: done"),
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    assert is_ok(approve_picture(store, actor="human"))
    ctx2 = Context(store=store, manifest=manifest, pack=pack, actor="human")
    try:
        ctx2.index.sync()
        assert query.result_adoption_for(ctx2.index, second_done, "sprint") is None
        codes2 = {i["code"] for i in run_doctor(store, manifest, pack, ctx2.index)["issues"]}
        assert "gate_bypassed" in codes2
    finally:
        ctx2.close()

    # Reopened adopted history requires a new result.
    assert is_ok(_move(store, done_task, "todo"))
    assert is_ok(_move(store, done_task, "doing"))
    reopened_move = _move(store, done_task, "done")
    assert reopened_move["ok"] is False
    assert reopened_move["error"]["code"] == "BUSINESS_RULE_VIOLATED"

    record_result_for_test(store, done_task, actor="implementer", outcome="completed")
    assert is_ok(_move(store, done_task, "done"))


def test_pack_switch_and_upgrade_preserve_complete_record_inventory_and_locations(
    tmp_path: Path,
) -> None:
    store = init_pack_store(
        tmp_path,
        result_pack_document("srcpack"),
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=True,
    )
    task = invoke("add", {"preset": "task", "title": "Inventory task"}, store=store, actor="cto")["value"]["slug"]
    assert is_ok(_move(store, task, "doing"))
    res = record_result_for_test(store, task, actor="implementer", outcome="completed")
    notes = write_review_notes_for_test(store, task, tmp_path)
    rev = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rev), rev
    assert is_ok(_move(store, task, "done"))

    before_inventory = record_inventory(store)
    assert res["record"] in before_inventory
    assert rev["value"]["record"] in before_inventory

    # Switch to a different pack with distinct result placements.
    target = result_pack_document("dstpack", producer_preset="output", review_preset="verdict")
    target["presets"]["finding"] = target["presets"].pop("output")
    target["presets"]["assessment"] = target["presets"].pop("verdict")
    target["results"]["produce"]["preset"] = "finding"
    target["results"]["review"]["preset"] = "assessment"
    target["workflows"]["bugfix"]["results"]["produce"]["preset"] = "finding"
    write_pack(store, "dstpack", target)
    tpl_dir = store / "packs/dstpack/templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (tpl_dir / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )

    switched = invoke(
        "pack use",
        {
            "name": "dstpack",
            "expect": etag((store / "rail.yaml").read_bytes()),
            "confirm": "USE PACK dstpack",
            "map": [],
        },
        store=store,
        actor="human",
    )
    assert is_ok(switched), switched
    assert is_ok(approve_picture(store, actor="human"))

    after_switch_inventory = record_inventory(store)
    assert all(after_switch_inventory[k] == v for k, v in before_inventory.items())

    # Upgrade also preserves all records and paths.
    upgraded = _upgrade(store, confirm=PHRASE)
    assert is_ok(upgraded), upgraded
    after_upgrade_inventory = record_inventory(store)
    assert all(after_upgrade_inventory[k] == v for k, v in after_switch_inventory.items())


def test_done_to_unfinished_pack_switch_starts_new_work_cycle(
    tmp_path: Path,
) -> None:
    src = result_pack_document("pack-a")
    src["workflows"]["sprint"]["transitions"] = {"todo": ["doing"], "doing": ["done"], "done": []}
    store = init_pack_store(
        tmp_path,
        src,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=True,
    )
    task = invoke("add", {"preset": "task", "title": "Cycle task"}, store=store, actor="cto")["value"]["slug"]
    assert is_ok(_move(store, task, "doing"))
    record_result_for_test(store, task, actor="implementer", outcome="completed")
    assert is_ok(_move(store, task, "done"))

    # Switch to a pack where ``done`` belongs to an active (unfinished) category
    # via ``sprint:done=doing`` (workflow-qualified mapping supports explicit
    # cross-category reopening across packs, unlike unqualified ``done=doing``)
    # while keeping item/artifact semantics identical.
    dst = result_pack_document("pack-b")
    dst["workflows"]["sprint"]["transitions"] = {"todo": ["doing"], "doing": ["done"], "done": []}
    write_pack(store, "pack-b", dst)
    tpl_dir = store / "packs/pack-b/templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (tpl_dir / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )

    switched = invoke(
        "pack use",
        {
            "name": "pack-b",
            "expect": etag((store / "rail.yaml").read_bytes()),
            "confirm": "USE PACK pack-b",
            "map": ["sprint:done=doing"],
        },
        store=store,
        actor="human",
    )
    assert is_ok(switched), switched
    assert switched["value"]["changes"] == [
        {
            "slug": task,
            "from": "done",
            "to": "doing",
            "from_category": "done",
            "to_category": "active",
        }
    ]
    assert is_ok(approve_picture(store, actor="human"))

    # Old completed result cannot close the reopened cycle.
    refused = _move(store, task, "done")
    assert refused["ok"] is False
    assert refused["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    reasons = refused["error"]["details"]["reasons"]
    assert any("work cycle" in r or "cycle" in r for r in reasons), reasons

    # Recording a fresh result in the new cycle succeeds.
    fresh = record_result_for_test(store, task, actor="implementer", outcome="completed")
    rec_row = records_of(store, "result.recorded")[-1]
    assert rec_row["data"]["work_cycle"] == switched["value"]["record"]
    assert fresh["record"] == rec_row["id"]
    assert is_ok(_move(store, task, "done"))
