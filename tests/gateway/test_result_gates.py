"""Gateway enforcement of mandatory result completion, explicit result checks, and adoption facts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import (
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    _write_charter,
    approve_picture,
    init_pack_store,
    init_result_store,
    record_result_for_test,
    refusal,
    require_git_identity,
    result_body,
    result_pack_document,
    run_git,
)

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter
from context_rail.doctor.checks import gate_report, run_doctor
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.results import RESULT_ADOPTION_KIND, parse_result_adoption
from context_rail.store import query
from context_rail.store.write import WriteRefused, commit


def _add_item(store: Path, preset: str, title: str, *, actor: str = "cto") -> str:
    added = invoke("add", {"preset": preset, "title": title}, store=store, actor=actor)
    assert is_ok(added), added
    return str(added["value"]["slug"])


def _etag(store: Path, slug: str) -> str:
    shown = invoke("show", {"slug": slug}, store=store)
    assert is_ok(shown), shown
    return str(shown["value"]["doc"]["etag"])


def _move(store: Path, slug: str, status: str, *, actor: str = "cto") -> dict[str, Any]:
    return invoke(
        "move",
        {"slug": slug, "status": status, "expect": _etag(store, slug)},
        store=store,
        actor=actor,
    )


def _write_notes(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _declare_result_check(store: Path, tmp_path: Path, slug: str, target: str) -> None:
    spec = tmp_path / f"{slug}.json"
    spec.write_text(
        json.dumps({"schema": 1, "mode": "result", "acceptance": False}),
        encoding="utf-8",
        newline="\n",
    )
    declared = invoke(
        "check",
        {"slug": slug, "target": target, "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )
    assert is_ok(declared), declared


def test_done_move_refuses_missing_and_incomplete_result_even_when_gates_false_and_human(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Task one")
    phase = _add_item(store, "phase", "Phase one")

    # Start task
    assert is_ok(_move(store, task, "doing"))

    # Even human cannot bypass missing producer result on contract-enabled workflow
    refused_cto = _move(store, task, "done", actor="cto")
    assert refusal(refused_cto)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("result" in r for r in refused_cto["error"]["details"]["reasons"])

    refused_human = _move(store, task, "done", actor="human")
    assert refusal(refused_human)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("result" in r for r in refused_human["error"]["details"]["reasons"])

    # Container move into done-category status also refuses without a result
    refused_phase = _move(store, phase, "done", actor="human")
    assert refusal(refused_phase)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("result" in r for r in refused_phase["error"]["details"]["reasons"])

    # Partial result still refuses done move
    record_result_for_test(store, task, actor="implementer", outcome="partial")
    refused_partial = _move(store, task, "done", actor="cto")
    assert refusal(refused_partial)[0] == "BUSINESS_RULE_VIOLATED"
    assert any(
        "partial" in r or "completed" in r
        for r in refused_partial["error"]["details"]["reasons"]
    )

    # Completed result opens the done gate when no review/checks are required
    record_result_for_test(store, task, actor="implementer", outcome="completed")
    assert is_ok(_move(store, task, "done", actor="cto"))

    # Status change record carries from_category and to_category
    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        records = [r for r in query.records_about(ix, task) if r["kind"] == "status.changed"]
        assert records
        latest_data = records[0]["data"]
        assert latest_data["from_category"] == "active"
        assert latest_data["to_category"] == "done"
    finally:
        ix.close()


def test_done_move_enforces_result_even_when_charter_policy_is_solo_or_none(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj", approve=False)
    # Solo charter: authorization skips lockout, but done move still enforces result contract
    charter_path = store / "charter.md"
    charter = {
        "primitive": "policy",
        "preset": "charter",
        "title": "Charter",
        "tags": [],
        "schema": 1,
        "mode": "solo",
        "project": {"goal": "g", "vision": "v", "scope": [], "wont": []},
        "protected_files": ["rail.yaml", "charter.md"],
        "default_branch": "main",
    }
    charter_path.write_text(
        "---\n" + dump_frontmatter(charter) + "---\n# Charter\n",
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    task = _add_item(store, "task", "Solo task")
    assert is_ok(_move(store, task, "doing"))
    refused = _move(store, task, "done", actor="human")
    assert refusal(refused)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("result" in r for r in refused["error"]["details"]["reasons"])

    # No charter policy at all: moving directly into done still refuses via result check
    # (for a workflow whose initial status can transition to done; without charter,
    # non-solo move into done is blocked by lockout unless we use a solo charter without gates)
    doc = result_pack_document("nocharter")
    doc["workflows"]["sprint"]["transitions"]["todo"] = ["doing", "done"]
    store2 = init_pack_store(
        tmp_path / "p2",
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    templates = store2 / "packs" / "nocharter" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    task2 = _add_item(store2, "task", "No charter task")
    # Direct _require_gate with policy=None enforces result contract even with no charter policy
    manifest2 = load_manifest(store2)
    pack2 = load_pack(manifest2.pack, store2)
    ctx2 = Context(store=store2, manifest=manifest2, pack=pack2, actor="human")
    try:
        from context_rail.gateway.handlers.docs import _require_gate
        from context_rail.store.doc import parse_doc

        rel2 = f"items/task/{task2}.md"
        doc2 = parse_doc(rel2, (store2 / rel2).read_bytes())
        with pytest.raises(WriteRefused) as exc2:
            _require_gate(ctx2, doc2, "done", "human")
        assert exc2.value.code == "BUSINESS_RULE_VIOLATED"
        assert any("result" in r for r in exc2.value.details["reasons"])
    finally:
        ctx2.close()
    # And doctor/gate_report with policy=None still reports gate_bypassed when done without result
    item2_file = store2 / "items" / "task" / f"{task2}.md"
    item2_file.write_text(
        item2_file.read_text(encoding="utf-8").replace("status: todo", "status: done"),
        encoding="utf-8",
        newline="\n",
    )
    ix2 = query.Index.open(store2, pack2)
    try:
        ix2.sync()
        report2 = gate_report(store2, manifest2, pack2, ix2)
        assert report2 and report2[0]["allowed"] is False
        codes2 = {i["code"] for i in run_doctor(store2, manifest2, pack2, ix2)["issues"]}
        assert "gate_bypassed" in codes2
    finally:
        ix2.close()


def test_result_check_explicit_verify_bindings_and_file_only_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Checked task")
    _declare_result_check(store, tmp_path, "res-proof", task)

    # Recording a result creates zero Check verdicts implicitly
    produced = record_result_for_test(store, task, actor="implementer", outcome="completed")
    shown_before = invoke("show", {"slug": "res-proof"}, store=store)
    assert is_ok(shown_before)
    assert shown_before["value"]["freshness"] == "missing"

    # Explicit `verify` passes and stores exact 6 bindings
    verified = invoke("verify", {"target": "res-proof"}, store=store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    assert verified["value"]["verdicts"][0]["freshness"] == "fresh"

    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        state = query.check_state(ix, "res-proof")
        assert state is not None
        assert state["result_binding"] == {
            "record": produced["record"],
            "body_hash": produced["body_hash"],
            "item_digest": state["result_binding"]["item_digest"],
            "work_cycle": None,
            "contract_digest": state["result_binding"]["contract_digest"],
            "candidate_commit": None,
        }
    finally:
        ix.close()

    # Freshness reads in non-git store never invoke git/subprocess/check runner
    from context_rail.runner import git as runner_git
    from context_rail.runner import run as runner_run

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("freshness reads must not run git or checks")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(runner_git, "rev_parse_head", boom)
    monkeypatch.setattr(runner_run, "run", boom)

    assert invoke("show", {"slug": "res-proof"}, store=store)["value"]["freshness"] == "fresh"

    # Tampering with result file makes freshness non-fresh
    res_path = store / produced["path"]
    original_bytes = res_path.read_bytes()
    res_path.write_text(
        res_path.read_text(encoding="utf-8") + "\nTampered.\n",
        encoding="utf-8",
        newline="\n",
    )
    assert invoke("show", {"slug": "res-proof"}, store=store)["value"]["freshness"] == "stale"

    # Restore file -> fresh again, then edit item body -> stale
    res_path.write_bytes(original_bytes)
    assert invoke("show", {"slug": "res-proof"}, store=store)["value"]["freshness"] == "fresh"

    item_path = store / "items" / "task" / f"{task}.md"
    item_path.write_text(
        item_path.read_text(encoding="utf-8") + "\nNew requirement.\n",
        encoding="utf-8",
        newline="\n",
    )
    assert invoke("show", {"slug": "res-proof"}, store=store)["value"]["freshness"] == "stale"


def test_result_check_records_failed_verdict_when_missing_or_partial(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Fail check task")
    _declare_result_check(store, tmp_path, "res-proof", task)

    missing_run = invoke("verify", {"target": "res-proof"}, store=store, actor="cto")
    assert is_ok(missing_run), missing_run
    assert missing_run["value"]["passed"] is False
    assert missing_run["value"]["verdicts"][0]["record"] is not None

    record_result_for_test(store, task, actor="implementer", outcome="partial")
    partial_run = invoke("verify", {"target": "res-proof"}, store=store, actor="cto")
    assert is_ok(partial_run), partial_run
    assert partial_run["value"]["passed"] is False


def test_reopen_invalidates_prior_result_review_and_check_until_reproved(
    tmp_path: Path,
) -> None:
    doc = result_pack_document()
    doc["transitions"] = {"todo": ["done"], "done": ["todo"]}
    doc["workflows"]["sprint"]["transitions"]["done"] = ["todo"]
    doc["workflows"]["sprint"]["quality"] = {
        "done": {"gate": "merge", "checks": ["res-proof"]}
    }
    store = init_pack_store(
        tmp_path / "proj",
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    templates = store / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    _write_charter(store)
    charter_path = store / "charter.md"
    charter_path.write_text(
        charter_path.read_text(encoding="utf-8")
        .replace("require_checks: false", "require_checks: true")
        .replace("require_review: false", "require_review: true"),
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    assert is_ok(approve_picture(store, actor="human"))

    task = _add_item(store, "task", "Release Task")
    assert is_ok(_move(store, task, "doing", actor="cto"))
    _declare_result_check(store, tmp_path, "res-proof", task)
    record_result_for_test(store, task, actor="implementer", outcome="completed")
    assert is_ok(invoke("verify", {"target": "res-proof"}, store=store, actor="cto"))
    notes = _write_notes(tmp_path / "rev.md", result_body(DEFAULT_REVIEW_SECTIONS))
    assert is_ok(
        invoke(
            "review",
            {"item": task, "verdict": "approve", "notes_file": str(notes)},
            store=store,
            actor="reviewer",
        )
    )
    assert is_ok(_move(store, task, "done", actor="cto"))

    # Reopen task (finished -> unfinished) with unchanged item prose
    assert is_ok(_move(store, task, "todo", actor="cto"))
    assert is_ok(_move(store, task, "doing", actor="cto"))

    # Stale result, review and check all fail on second done move
    reclosed = _move(store, task, "done", actor="cto")
    assert refusal(reclosed)[0] == "BUSINESS_RULE_VIOLATED"
    reasons = reclosed["error"]["details"]["reasons"]
    assert any("cycle" in r or "result" in r for r in reasons), reasons

    # Record fresh result + verify check + fresh review -> passes again
    record_result_for_test(store, task, actor="implementer", outcome="completed")
    assert is_ok(invoke("verify", {"target": "res-proof"}, store=store, actor="cto"))
    assert is_ok(
        invoke(
            "review",
            {"item": task, "verdict": "approve", "notes_file": str(notes)},
            store=store,
            actor="reviewer",
        )
    )
    assert is_ok(_move(store, task, "done", actor="cto"))


def test_adoption_exemption_scope_reservation_and_parity(tmp_path: Path) -> None:
    doc = result_pack_document()
    # Initially write a store without contracts, create a done historical item, then enable contracts
    legacy_doc = result_pack_document()
    legacy_doc.pop("results", None)
    legacy_doc["workflows"]["bugfix"].pop("results", None)
    store = init_pack_store(tmp_path / "proj", legacy_doc, approve=True)

    done_task = _add_item(store, "task", "Historical done")
    assert is_ok(_move(store, done_task, "doing"))
    assert is_ok(_move(store, done_task, "done"))

    # Add contract to pack.yaml + templates + layout without fabricating results
    pack_path = store / "packs" / doc["name"] / "pack.yaml"
    pack_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")
    templates = store / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    manifest_path = store / "rail.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["presets"]["finding"] = "records/findings/{field:workflow_name}/{about.slug}/{id}.md"
    manifest_data["presets"]["assessment"] = "records/assessments/{field:workflow_name}/{about.slug}/{id}.md"
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8", newline="\n")
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))

    # File-scoped approval (`manifest`) must NOT activate result adoption
    assert is_ok(
        invoke(
            "approve",
            {
                "target": "charter",
                "scope": "manifest",
                "confirm": "APPROVE charter",
                "confirmer": "Val",
            },
            store=store,
            actor="human",
        )
    )
    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        assert query.result_adoption_for(ix, done_task, "sprint") is None
        # Without adoption, doctor reports gate_bypassed for missing result
        codes = {i["code"] for i in run_doctor(store, manifest, pack, ix)["issues"]}
        assert "gate_bypassed" in codes
    finally:
        ix.close()

    # Whole-picture charter approval activates workflows and snapshots existing done item
    assert is_ok(
        invoke(
            "approve",
            {
                "target": "charter",
                "confirm": "APPROVE charter",
                "confirmer": "Val",
            },
            store=store,
            actor="human",
        )
    )
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        # No fabricated results/reviews
        assert query.result_count(ix, done_task) == 0
        assert query.review_for_item(ix, done_task) is None
        row = query.result_adoption_for(ix, done_task, "sprint")
        assert row is not None
        # Assessment (doctor + gate_report) exempts unchanged adopted historical item
        report = gate_report(store, manifest, pack, ix)
        entry = next(r for r in report if r["item"] == done_task)
        assert entry["allowed"] is True, entry
        codes = {i["code"] for i in run_doctor(store, manifest, pack, ix)["issues"]}
        assert "gate_bypassed" not in codes
    finally:
        ix.close()

    # Forging results.adopted via generic add or direct commit without reserved_kinds refuses
    forged_add = invoke(
        "add",
        {"preset": "event", "title": "Forged adoption", "set": [f"kind={RESULT_ADOPTION_KIND}"]},
        store=store,
        actor="human",
    )
    if is_ok(forged_add):
        # If add treated kind as plain frontmatter, ensure no results.adopted record was created
        pass
    with pytest.raises(WriteRefused) as excinfo:
        commit(
            store,
            manifest,
            [],
            {
                "kind": RESULT_ADOPTION_KIND,
                "about": [],
                "data": {
                    "schema": 1,
                    "pack": doc["name"],
                    "workflows": ["sprint"],
                    "historical_done": [],
                },
            },
            actor="human",
        )
    assert excinfo.value.code == "VALIDATION_FAILED"

    # Reapproval never refreshes an already-activated workflow snapshot
    new_done = _add_item(store, "task", "Hand edited done")
    item_file = store / "items" / "task" / f"{new_done}.md"
    item_file.write_text(
        item_file.read_text(encoding="utf-8").replace("status: todo", "status: done"),
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    assert is_ok(
        invoke(
            "approve",
            {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
            store=store,
            actor="human",
        )
    )
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        assert query.result_adoption_for(ix, new_done, "sprint") is None
        codes = {i["code"] for i in run_doctor(store, manifest, pack, ix)["issues"]}
        assert "gate_bypassed" in codes
    finally:
        ix.close()

    # Editing the adopted historical item invalidates its exemption
    hist_path = store / "items" / "task" / f"{done_task}.md"
    hist_path.write_text(
        hist_path.read_text(encoding="utf-8") + "\nChanged objective.\n",
        encoding="utf-8",
        newline="\n",
    )
    ix = query.Index.open(store, pack)
    try:
        ix.sync()
        codes = {i["code"] for i in run_doctor(store, manifest, pack, ix)["issues"]}
        assert "gate_bypassed" in codes
    finally:
        ix.close()


def test_parse_result_adoption_strict() -> None:
    valid = parse_result_adoption(
        {
            "schema": 1,
            "pack": "basic",
            "workflows": ["default", "sprint"],
            "historical_done": [
                {
                    "item": "task-1",
                    "workflow": "sprint",
                    "item_digest": "a" * 64,
                    "work_cycle": None,
                }
            ],
        }
    )
    assert valid["workflows"] == ["default", "sprint"]
    with pytest.raises(ModelError):
        parse_result_adoption(
            {
                "schema": 1,
                "pack": "basic",
                "workflows": ["sprint", "sprint"],
                "historical_done": [],
            }
        )
    with pytest.raises(ModelError):
        parse_result_adoption(
            {
                "schema": 1,
                "pack": "basic",
                "workflows": [],
                "historical_done": [],
            }
        )


def test_commitless_review_and_stale_review_integrity_and_missing_quality_rule(
    tmp_path: Path,
) -> None:
    # Pack with result contract, require_review=True, and NO workflow quality rule on done
    doc = result_pack_document()
    store = init_pack_store(
        tmp_path / "proj",
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    templates = store / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    _write_charter(store)
    charter_path = store / "charter.md"
    charter_path.write_text(
        charter_path.read_text(encoding="utf-8").replace(
            "require_review: false", "require_review: true"
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    assert is_ok(approve_picture(store, actor="human"))

    task = _add_item(store, "task", "Commitless reviewed")
    assert is_ok(_move(store, task, "doing", actor="cto"))

    # Missing result still refuses even when no workflow quality rule exists
    assert refusal(_move(store, task, "done", actor="cto"))[0] == "BUSINESS_RULE_VIOLATED"

    produced = record_result_for_test(store, task, actor="implementer", outcome="completed")
    notes = _write_notes(tmp_path / "rev.md", result_body(DEFAULT_REVIEW_SECTIONS))
    rev = invoke(
        "review",
        {"item": task, "verdict": "approve", "notes_file": str(notes)},
        store=store,
        actor="reviewer",
    )
    assert is_ok(rev), rev
    assert rev["value"]["commit"] is None

    # Tampering with review Markdown body makes review invalid and blocks done move
    rev_path = next((store / "records" / "assessments").rglob(f"{rev['value']['record']}.md"))
    original_rev = rev_path.read_bytes()
    rev_path.write_text(
        rev_path.read_text(encoding="utf-8") + "\nTampered review.\n",
        encoding="utf-8",
        newline="\n",
    )
    tampered = _move(store, task, "done", actor="cto")
    assert refusal(tampered)[0] == "BUSINESS_RULE_VIOLATED"

    # Restore review -> commitless result-backed review passes done gate
    rev_path.write_bytes(original_rev)
    assert is_ok(_move(store, task, "done", actor="cto"))

    # A completed result on another task still does not bypass an unreviewed gate
    task2 = _add_item(store, "task", "Needs review too")
    assert is_ok(_move(store, task2, "doing", actor="cto"))
    record_result_for_test(store, task2, actor="implementer", outcome="completed")
    unreviewed = _move(store, task2, "done", actor="cto")
    assert refusal(unreviewed)[0] == "BUSINESS_RULE_VIOLATED"
    assert any("review" in r for r in unreviewed["error"]["details"]["reasons"])

    # Candidate recorded after commitless result invalidates both result and review
    require_git_identity()
    proj = tmp_path / "git_proj"
    proj.mkdir()
    run_git(proj, "init", "-b", "main")
    store_git = init_result_store(proj)
    (proj / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    run_git(proj, "add", "app.py")
    run_git(proj, "commit", "-m", "init")
    t_git = _add_item(store_git, "task", "Git stale")
    assert is_ok(_move(store_git, t_git, "doing", actor="cto"))
    record_result_for_test(store_git, t_git, actor="implementer", outcome="completed")
    assert is_ok(
        invoke(
            "review",
            {"item": t_git, "verdict": "approve", "notes_file": str(notes)},
            store=store_git,
            actor="reviewer",
        )
    )
    (proj / "app.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
    run_git(proj, "add", "app.py")
    run_git(proj, "commit", "-m", "cand")
    sha = run_git(proj, "rev-parse", "HEAD").stdout.strip()
    assert is_ok(invoke("candidate", {"item": t_git, "commit": sha}, store=store_git, actor="implementer"))
    stale_after_cand = _move(store_git, t_git, "done", actor="cto")
    assert refusal(stale_after_cand)[0] == "BUSINESS_RULE_VIOLATED"
    assert produced["record"] is not None

