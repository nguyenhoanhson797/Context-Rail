"""Bounded, reach-filtered result context, summaries, and contract delivery tests."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import (
    BUGFIX_PRODUCER_SECTIONS,
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    as_human,
    as_seat,
    init_pack_store,
    init_result_store,
    open_test_session,
    record_result_for_test,
    refusal,
    require_git_identity,
    result_body,
    result_pack_document,
    run_git,
)

from context_rail.context import results as result_ctx
from context_rail.context import surface
from context_rail.context.seat_brief import seat_brief_for
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.model.results import RESULT_BODY_MAX_BYTES
from context_rail.runner import git as runner_git
from context_rail.ui import readmodels, snapshot


@contextlib.contextmanager
def _open_ctx(store: Path, *, actor: str = "cto", seat: str | None = None):
    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    ctx = Context(store=store, manifest=manifest, pack=pack, actor=actor)
    try:
        if seat is not None:
            from context_rail.gateway.identity import resolve_identity

            token = open_test_session(store, seat)
            ctx.identity = resolve_identity(ctx, actor=None, session=token)
            ctx.actor = ctx.identity.name
        ctx.index.sync()
        yield ctx
    finally:
        ctx.close()


def _add_item(store: Path, preset: str, title: str, *, rel: list[str] | None = None) -> str:
    args: dict[str, Any] = {"preset": preset, "title": title}
    if rel:
        args["rel"] = rel
    added = invoke("add", args, store=store, actor="cto")
    assert is_ok(added), added
    return str(added["value"]["slug"])


def _write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _init_role_result_store(tmp_path: Path, *, different_model: bool = False) -> tuple[Path, dict[str, str]]:
    doc = result_pack_document()
    doc["presets"]["phase"] = {"primitive": "item", "tags": ["phase"], "container": True}
    doc["presets"]["finding"]["required"] = ["workflow_name", "channel"]
    doc["presets"]["finding"]["defaults"] = {"channel": "research"}
    doc["presets"]["finding"]["fields"] = {"channel": ["research", "code"]}
    doc["operating"] = {
        "version": 2,
        "unit": "task",
        "hierarchy": [{"preset": "phase", "required": True}],
        "roles": {
            "lead": {
                "anchor": "project",
                "reach": {"read": ["project"], "write": ["project"], "code": {"read": ["**"], "write": []}},
                "can": {
                    "add": ["phase", "task", "bug"],
                    "result": ["phase", "task", "bug"],
                    "review": ["phase", "task", "bug"],
                    "grant": ["phase-lead", "implementer", "reviewer"],
                },
                "sees": {"up": 0, "down": 1, "parts": ["title", "status", "next", "results"]},
            },
            "phase-lead": {
                "anchor": {"level": "phase"},
                "reach": {
                    "read": ["anchor", {"up": 1}],
                    "write": ["anchor"],
                    "code": {"read": ["**"], "write": []},
                },
                "can": {
                    "add": ["task", "bug"],
                    "result": ["task", "bug"],
                    "grant": ["implementer", "reviewer"],
                },
                "sees": {"up": 1, "down": "all", "parts": ["title", "status", "next", "results"]},
            },
            "implementer": {
                "anchor": {"item": ["task", "bug"]},
                "reach": {
                    "read": ["anchor", {"up": 1}],
                    "write": ["anchor"],
                    "code": {"read": ["src/**"], "write": ["src/**"]},
                },
                "can": {"result": ["task", "bug"], "candidate": ["task", "bug"]},
                "sees": {"up": 1, "parts": ["title", "body", "results", "code_area"]},
            },
            "reviewer": {
                "anchor": {"item": ["task", "bug"]},
                "reach": {
                    "read": ["anchor", {"up": 1}],
                    "write": ["anchor"],
                    "code": {"read": ["src/**"], "write": []},
                },
                "independent_of": ["produce"],
                "can": {"review": ["task", "bug"]},
                "sees": {"up": 1, "parts": ["title", "diff", "results", "evidence", "code_area"]},
            },
        },
    }
    project = tmp_path / "role_proj"
    templates = project / "context-rail" / "packs" / doc["name"] / "templates"
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
    store = init_pack_store(
        project,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    from helpers import _write_charter, approve_picture

    _write_charter(store)
    charter_path = store / "charter.md"
    charter_path.write_text(
        charter_path.read_text(encoding="utf-8").replace(
            "default_branch: main\n",
            f"default_branch: main\nreviewer_different_model: {'true' if different_model else 'false'}\n",
        ),
        encoding="utf-8",
        newline="\n",
    )
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        assert is_ok(approve_picture(store, actor=None))
        p_a = invoke("add", {"preset": "phase", "title": "Component A"}, store=store, actor=None)["value"]["slug"]
        p_b = invoke("add", {"preset": "phase", "title": "Component B"}, store=store, actor=None)["value"]["slug"]
        t_a = invoke("add", {"preset": "task", "title": "Task A", "rel": [f"part_of={p_a}"]}, store=store, actor=None)["value"]["slug"]
        t_b = invoke("add", {"preset": "task", "title": "Task B", "rel": [f"part_of={p_b}"]}, store=store, actor=None)["value"]["slug"]
        b_a = invoke("add", {"preset": "bug", "title": "Bug A", "rel": [f"part_of={p_a}"]}, store=store, actor=None)["value"]["slug"]
        assert is_ok(invoke("seat grant", {"seat": "lead-1", "role": "lead", "anchor": "project"}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "pl-a", "role": "phase-lead", "anchor": p_a}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "pl-b", "role": "phase-lead", "anchor": p_b}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "impl-a", "role": "implementer", "anchor": t_a}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "rev-a", "role": "reviewer", "anchor": t_a}, store=store, actor=None))
    return store, {"p_a": p_a, "p_b": p_b, "t_a": t_a, "t_b": t_b, "b_a": b_a}


def test_worker_brief_lists_contract_headings_metadata_and_command(tmp_path: Path) -> None:
    store, items = _init_role_result_store(tmp_path)
    brief = as_seat(store, "impl-a", "brief", {"item": items["t_a"]})
    assert is_ok(brief), brief
    text = brief["value"]["text"]
    for heading in DEFAULT_PRODUCER_SECTIONS:
        assert heading in text
    assert "channel" in text
    assert "research" in text
    assert "rail loop result" in text

    with as_human():
        bug_brief = invoke("brief", {"item": items["b_a"], "role": "implementer"}, store=store, actor=None)
    assert is_ok(bug_brief), bug_brief
    for heading in BUGFIX_PRODUCER_SECTIONS:
        assert heading in bug_brief["value"]["text"]


def test_reviewer_brief_in_non_git_store_delivers_commitless_result_and_refuses_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, items = _init_role_result_store(tmp_path)

    def boom(*args: object, **kwargs: object) -> Any:
        raise AssertionError("brief must not call git/process/head")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(runner_git, "diff_text", boom)
    monkeypatch.setattr(runner_git, "resolve_commit", boom)

    empty = as_seat(store, "rev-a", "brief", {"item": items["t_a"]})
    assert refusal(empty)[0] == "INVALID_STATE"
    assert "result" in empty["error"]["message"].lower()

    body = _write_file(tmp_path / "prod.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Investigated auth flow."}))
    produced = as_seat(
        store,
        "impl-a",
        "result",
        {"item": items["t_a"], "body_file": str(body), "outcome": "completed"},
    )
    assert is_ok(produced), produced
    rec = produced["value"]

    reviewed = as_seat(store, "rev-a", "brief", {"item": items["t_a"]})
    assert is_ok(reviewed), reviewed
    val = reviewed["value"]
    assert val["candidate"] is None
    assert rec["record"] in val["text"]
    assert rec["path"] in val["text"]
    assert "impl-a" in val["text"]
    assert "completed" in val["text"]
    assert "Investigated auth flow." in val["text"]
    assert "rail loop review" in val["text"]


def test_reviewer_brief_with_candidate_missing_result_is_diagnostic_not_approval(
    tmp_path: Path,
) -> None:
    require_git_identity()
    store, items = _init_role_result_store(tmp_path)
    root = store.parent
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run_git(root, "init", "-b", "main")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
    (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(root, "add", "src/app.py")
    run_git(root, "commit", "-m", "work")

    cand = as_seat(store, "impl-a", "candidate", {"item": items["t_a"], "commit": "HEAD"})
    assert is_ok(cand), cand

    brief = as_seat(store, "rev-a", "brief", {"item": items["t_a"]})
    assert is_ok(brief), brief
    text = brief["value"]["text"]
    assert cand["value"]["record"] == brief["value"]["candidate"]
    assert "VALUE = 2" in text
    assert "missing" in text.lower() or "no producer result" in text.lower()
    assert "approve" not in brief["value"].get("result_state", {}).get("status", "")


def test_component_brief_does_not_leak_other_component_and_lead_summaries_match(
    tmp_path: Path,
) -> None:
    store, items = _init_role_result_store(tmp_path)
    body_a = _write_file(tmp_path / "a.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Alpha secret findings"}))
    body_b = _write_file(tmp_path / "b.md", result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Beta confidential findings"}))
    with as_human():
        res_a = invoke("result", {"item": items["t_a"], "body_file": str(body_a)}, store=store, actor=None)
        res_b = invoke("result", {"item": items["t_b"], "body_file": str(body_b)}, store=store, actor=None)
    assert is_ok(res_a) and is_ok(res_b)
    rec_a = res_a["value"]
    rec_b = res_b["value"]

    seat_a = as_seat(store, "pl-a", "cto brief", {})
    assert is_ok(seat_a), seat_a
    assert rec_a["record"] in seat_a["value"]["text"]
    assert rec_b["record"] not in seat_a["value"]["text"]
    assert rec_b["path"] not in seat_a["value"]["text"]
    assert "Beta confidential findings" not in seat_a["value"]["text"]

    # Even if a role tries to add global preset reach for finding, item reach still hides component B.
    pack_path = store / "packs" / load_manifest(store).pack / "pack.yaml"
    pack_doc = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_doc["operating"]["roles"]["phase-lead"]["reach"]["read"].append({"preset": ["finding"]})
    pack_path.write_text(yaml.safe_dump(pack_doc, sort_keys=False), encoding="utf-8", newline="\n")
    with _open_ctx(store, seat="pl-a") as ctx_a:
        summaries = result_ctx.result_summaries(ctx_a, [items["t_a"], items["t_b"]])
        assert [row["item"] for row in summaries] == [items["t_a"]]
        hidden = result_ctx.result_context(ctx_a, items["t_b"], include_body=True)
        assert hidden["selected"] is None
        assert hidden["omitted_outside_reach"] >= 1

    lead_brief = as_seat(store, "lead-1", "cto brief", {})
    assert is_ok(lead_brief), lead_brief
    assert rec_a["record"] in lead_brief["value"]["text"]
    assert rec_b["record"] in lead_brief["value"]["text"]


def test_trimmed_reviewer_brief_keeps_identity_and_read_pointer_and_honest_clipping(
    tmp_path: Path,
) -> None:
    store, items = _init_role_result_store(tmp_path)
    huge_text = "EVIDENCE_LINE " * 15000
    assert len(huge_text.encode("utf-8")) <= RESULT_BODY_MAX_BYTES
    body = _write_file(
        tmp_path / "huge.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Evidence": huge_text}),
    )
    produced = as_seat(store, "impl-a", "result", {"item": items["t_a"], "body_file": str(body)})
    assert is_ok(produced), produced
    rec = produced["value"]

    requested_budget = 900
    brief = as_seat(
        store,
        "rev-a",
        "brief",
        {"item": items["t_a"], "max_chars": requested_budget},
    )
    assert is_ok(brief), brief
    val = brief["value"]
    assert len(val["text"]) <= requested_budget
    assert rec["record"] in val["text"]
    assert rec["path"] in val["text"]
    assert val["omitted"]
    assert "clipped" in val["text"].lower() or "truncated" in val["text"].lower() or "omitted" in val["text"].lower()
    assert "rail read show" in val["text"] or rec["path"] in val["text"]

    # Also verify direct result_context and seat_brief_for clipping honesty
    with _open_ctx(store, seat="rev-a") as ctx_rev:
        rc = result_ctx.result_context(ctx_rev, items["t_a"], include_body=True, body_chars=300)
        assert rc["selected"]["record_id"] == rec["record"]
        assert rc["selected"]["path"] == rec["path"]
        assert rc["body_omitted_chars"] > 0
        assert rc["body_clipped"] is True

        sb = seat_brief_for(
            ctx_rev,
            role_name="reviewer",
            anchor=items["t_a"],
            profile=None,
            granted_by="lead-1",
            max_chars=700,
        )
        assert len(sb["text"]) <= 700
        assert rec["record"] in sb["text"]
        assert rec["path"] in sb["text"]


def test_surface_and_ui_expose_compiled_artifacts_and_completion_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = init_result_store(tmp_path / "proj")
    task = _add_item(store, "task", "Surface task")
    bug = _add_item(store, "bug", "Surface bug")

    with _open_ctx(store) as ctx:
        cat = surface.catalog(ctx)
        task_entry = next(entry for entry in cat["items"] if entry["name"] == "task")
        assert task_entry["results"]["produce"]["preset"] == "finding"
        assert task_entry["results"]["produce"]["sections"] == list(DEFAULT_PRODUCER_SECTIONS)

        exp_bug = surface.explain(ctx, "bugfix")
        assert exp_bug["results"]["produce"]["sections"] == list(BUGFIX_PRODUCER_SECTIONS)
        exp_preset = surface.explain(ctx, "bug")
        assert exp_preset["results"]["produce"]["sections"] == list(BUGFIX_PRODUCER_SECTIONS)

        card = surface.card(ctx, bug)
        assert card["results"]["produce"]["preset"] == "finding"
        assert card["results"]["produce"]["sections"] == list(BUGFIX_PRODUCER_SECTIONS)

        why = surface.why(ctx, task)
        assert why["result"]["required"] is True
        assert why["result"]["completed"] is False
        assert why["result"]["reasons"]

        model_view = invoke("model", {}, store=store, actor="cto")
        assert is_ok(model_view), model_view
        assert "results" in model_view["value"]["model"]

        shown = invoke("show", {"slug": task}, store=store, actor="cto")
        assert is_ok(shown), shown
        assert "results" in shown["value"]

        snap = snapshot.build(ctx)
        assert "results" in snap
        assert "bugfix" in snap["results"]
        assert snap["results"]["bugfix"]["produce"]["sections"] == list(BUGFIX_PRODUCER_SECTIONS)

        next_env = readmodels.read(ctx, "next")
        assert next_env["status"] == "ok"
        task_q = next(item for item in next_env["queue"] if item["id"] == task)
        assert "result" in task_q
        assert task_q["result"]["required"] is True
        assert task_q["result"]["completed"] is False

    # Record a completed result and verify surface/snapshot/readmodels reflect completion
    produced = record_result_for_test(store, task, actor="implementer", outcome="completed")
    with _open_ctx(store) as ctx:
        why_after = surface.why(ctx, task)
        assert why_after["result"]["completed"] is True
        assert why_after["result"]["record"] == produced["record"]
        shown_after = invoke("show", {"slug": task}, store=store, actor="cto")
        assert shown_after["value"]["results"]["selected"]["record_id"] == produced["record"]

        # Readmodels `_next` must use batched SQL summaries without opening per-item disk files.
        def no_disk_reads(self: Path, *args: object, **kwargs: object) -> bytes:
            raise AssertionError(f"readmodels._next must not read disk file {self}")

        monkeypatch.setattr(Path, "read_bytes", no_disk_reads)
        next_batched = readmodels.read(ctx, "next")
        assert next_batched["status"] == "ok"
        task_q_after = next(item for item in next_batched["queue"] if item["id"] == task)
        assert task_q_after["result"]["required"] is True
        assert task_q_after["result"]["record"] == produced["record"]
        assert task_q_after["result"]["completed"] is True


def test_worker_seat_brief_keeps_contract_after_partial_result(tmp_path: Path) -> None:
    store, items = _init_role_result_store(tmp_path)
    partial_body = _write_file(
        tmp_path / "partial.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Attempt 1 partial"}),
    )
    partial = as_seat(
        store,
        "impl-a",
        "result",
        {"item": items["t_a"], "body_file": str(partial_body), "outcome": "partial"},
    )
    assert is_ok(partial), partial

    brief = as_seat(store, "impl-a", "cto brief", {})
    assert is_ok(brief), brief
    text = brief["value"]["text"]
    for heading in DEFAULT_PRODUCER_SECTIONS:
        assert heading in text
    assert "rail loop result" in text
    assert partial["value"]["record"] in text
    assert "partial" in text


def test_tiny_budget_preserves_full_result_pointer_without_slicing(tmp_path: Path) -> None:
    store, items = _init_role_result_store(tmp_path)
    huge_text = "EVIDENCE_LINE " * 4000
    body = _write_file(
        tmp_path / "huge2.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Evidence": huge_text}),
    )
    produced = as_seat(store, "impl-a", "result", {"item": items["t_a"], "body_file": str(body)})
    assert is_ok(produced), produced
    rec = produced["value"]

    pointer_len = len(
        f"result: {rec['record']} | path: {rec['path']} | producer: impl-a | "
        f"outcome: completed | status: current, commitless | "
        f"read: rail read show {rec['record']} --path {rec['path']}"
    )
    budget = pointer_len + 80
    brief = as_seat(store, "rev-a", "brief", {"item": items["t_a"], "max_chars": budget})
    assert is_ok(brief), brief
    text = brief["value"]["text"]
    assert len(text) <= budget
    assert rec["record"] in text
    assert rec["path"] in text
    assert f"rail read show {rec['record']} --path {rec['path']}" in text

    sb = as_seat(store, "rev-a", "cto brief", {"max_chars": budget})
    assert is_ok(sb), sb
    assert len(sb["value"]["text"]) <= budget
    assert rec["record"] in sb["value"]["text"]
    assert rec["path"] in sb["value"]["text"]


def test_history_omitted_does_not_leak_restricted_records(tmp_path: Path) -> None:
    store, items = _init_role_result_store(tmp_path)
    for idx in range(2):
        body = _write_file(
            tmp_path / f"r{idx}.md",
            result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": f"Entry {idx}"}),
        )
        res = as_seat(store, "impl-a", "result", {"item": items["t_a"], "body_file": str(body)})
        assert is_ok(res), res

    pack_path = store / "packs" / load_manifest(store).pack / "pack.yaml"
    pack_doc = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_doc["operating"]["roles"]["phase-lead"]["reach"]["read"].append(
        {"path": "records/other/**"}
    )
    pack_path.write_text(yaml.safe_dump(pack_doc, sort_keys=False), encoding="utf-8", newline="\n")

    with _open_ctx(store, seat="pl-a") as ctx_a:
        ctx = result_ctx.result_context(ctx_a, items["t_a"], include_body=False)
        assert ctx["selected"] is None
        assert ctx["history"] == []
        assert ctx["history_omitted"] == 0
