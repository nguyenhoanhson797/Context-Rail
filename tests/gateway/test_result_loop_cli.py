"""Real CLI end-to-end tests for commitless research and commit-bound code result loops."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from helpers import (
    DEFAULT_PRODUCER_SECTIONS,
    DEFAULT_REVIEW_SECTIONS,
    _write_charter,
    approve_picture,
    init_pack_store,
    record_inventory,
    require_git_identity,
    result_body,
    result_pack_document,
    run_cli,
    run_git,
)

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _cli_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert proc.stdout.strip(), proc.stderr
    return json.loads(proc.stdout)


def _add_result_check(store: Path, tmp_path: Path, item_slug: str, check_slug: str) -> None:
    spec_path = tmp_path / f"{check_slug}.json"
    spec_path.write_text(
        json.dumps({"schema": 1, "mode": "result", "acceptance": False}),
        encoding="utf-8",
        newline="\n",
    )
    proc = run_cli(
        store.parent,
        "--actor",
        "cto",
        "check",
        "declare",
        check_slug,
        "--target",
        item_slug,
        "--spec-file",
        str(spec_path),
        "--expect",
        "absent",
        store=store,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _cli_json(proc)["ok"] is True


def _write_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _configure_result_store(tmp_path: Path, *, with_git: bool = False) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    if with_git:
        require_git_identity()
        run_git(project, "init", "-b", "main")

    doc = result_pack_document()
    doc["workflows"]["sprint"]["transitions"]["done"] = ["doing"]
    store = project / "context-rail"
    templates = store / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {section}\n" for section in sections),
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
    _write_charter(store)
    charter_path = store / "charter.md"
    charter_text = charter_path.read_text(encoding="utf-8")
    charter_path.write_text(
        charter_text.replace(
            "require_checks: false\n", "require_checks: true\n"
        ).replace(
            "require_review: false\n", "require_review: true\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))
    approved = approve_picture(store)
    assert is_ok(approved), approved

    if with_git:
        (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
        run_git(project, "add", "-A")
        run_git(project, "commit", "-m", "initial")

    return project, store


def test_research_loop_cli_needs_no_git_candidate(tmp_path: Path) -> None:
    project, store = _configure_result_store(tmp_path, with_git=False)

    # 1. Register a discovery/search item under a phase container and start it.
    phase_res = _cli_json(
        run_cli(project, "--actor", "cto", "change", "add", "phase", "Discovery", store=store)
    )
    assert phase_res["ok"] is True, phase_res
    phase_slug = phase_res["value"]["slug"]

    item_res = _cli_json(
        run_cli(
            project,
            "--actor",
            "cto",
            "change",
            "add",
            "task",
            "Search auth architecture",
            "--rel",
            f"part_of={phase_slug}",
            store=store,
        )
    )
    assert item_res["ok"] is True, item_res
    item_slug = item_res["value"]["slug"]
    item_etag = item_res["value"]["etag"]

    doing_res = _cli_json(
        run_cli(
            project,
            "--actor",
            "cto",
            "change",
            "move",
            item_slug,
            "doing",
            "--expect",
            item_etag,
            store=store,
        )
    )
    assert doing_res["ok"] is True, doing_res

    # 2. Read the compiled work brief via CLI (never parsing pack.yaml).
    brief_proc = run_cli(
        project,
        "--actor",
        "implementer",
        "loop",
        "brief",
        item_slug,
        "--role",
        "implementer",
        store=store,
    )
    assert brief_proc.returncode == 0, brief_proc.stdout + brief_proc.stderr
    brief_res = _cli_json(brief_proc)
    assert brief_res["ok"] is True, brief_res
    assert "result" in brief_res["value"]["text"].lower()

    # 3. Record discovery findings via CLI without fabricating a candidate commit.
    body_path = _write_file(
        tmp_path / "discovery-result.md",
        result_body(
            DEFAULT_PRODUCER_SECTIONS,
            values={
                "Summary": "Mapped OAuth and session endpoints.",
                "Findings": "Two entry points share token validation.",
                "Evidence": "Inspected auth router and session store.",
                "Next steps": "Proceed to review.",
            },
        ),
    )
    result_proc = run_cli(
        project,
        "--actor",
        "implementer",
        "loop",
        "result",
        item_slug,
        "--body-file",
        str(body_path),
        "--outcome",
        "completed",
        "--model",
        "model-a",
        "--key",
        "res-discovery-1",
        store=store,
    )
    assert result_proc.returncode == 0, result_proc.stdout + result_proc.stderr
    result_payload = _cli_json(result_proc)
    assert result_payload["ok"] is True, result_payload
    result_receipt = result_payload["value"]
    assert result_receipt["candidate_commit"] is None
    assert (store / result_receipt["path"]).is_file()

    # Assert no candidate.recorded artifact was fabricated.
    inv_after_result = record_inventory(store)
    for rec_id, (rel, _) in inv_after_result.items():
        if rel.endswith(".json"):
            data = json.loads((store / rel).read_text(encoding="utf-8"))
            assert data.get("kind") != "candidate.recorded", (rec_id, rel)

    # 4. Declare and run a mode: result Check via CLI.
    _add_result_check(store, tmp_path, item_slug, "research-result-check")
    check_proc = run_cli(
        project, "--actor", "cto", "check", "run", item_slug, store=store
    )
    assert check_proc.returncode == 0, check_proc.stdout + check_proc.stderr
    check_payload = _cli_json(check_proc)
    assert check_payload["ok"] is True, check_payload
    assert check_payload["value"]["passed"] is True

    # 5. Separate actor submits structured Markdown review via CLI.
    notes_path = _write_file(
        tmp_path / "discovery-review.md",
        result_body(
            DEFAULT_REVIEW_SECTIONS,
            values={
                "Summary": "Verified auth discovery findings.",
                "Findings": "Entry points and evidence match item scope.",
                "Evidence": f"Reviewed {result_receipt['record']}.",
                "Verdict": "Approve discovery findings.",
            },
        ),
    )
    review_proc = run_cli(
        project,
        "--actor",
        "reviewer",
        "loop",
        "review",
        item_slug,
        "--verdict",
        "approve",
        "--notes-file",
        str(notes_path),
        "--model",
        "model-b",
        "--key",
        "rev-discovery-1",
        store=store,
    )
    assert review_proc.returncode == 0, review_proc.stdout + review_proc.stderr
    review_payload = _cli_json(review_proc)
    assert review_payload["ok"] is True, review_payload
    review_receipt = review_payload["value"]
    assert review_receipt["record"] != result_receipt["record"]
    assert review_receipt["path"] != result_receipt["path"]
    assert review_receipt["commit"] is None
    assert review_receipt["result"] == result_receipt["record"]

    # 6. Complete the item via CLI.
    show_proc = run_cli(project, "--actor", "cto", "read", "show", item_slug, store=store)
    current_etag = _cli_json(show_proc)["value"]["doc"]["etag"]
    move_proc = run_cli(
        project,
        "--actor",
        "cto",
        "change",
        "move",
        item_slug,
        "done",
        "--expect",
        current_etag,
        store=store,
    )
    assert move_proc.returncode == 0, move_proc.stdout + move_proc.stderr
    moved = _cli_json(move_proc)["value"]
    assert moved["status"] == "done"

    # 7. Reopen and record a newer partial attempt -> prior proof becomes stale.
    reopen_proc = run_cli(
        project,
        "--actor",
        "cto",
        "change",
        "move",
        item_slug,
        "doing",
        "--expect",
        moved["etag"],
        store=store,
    )
    assert reopen_proc.returncode == 0, reopen_proc.stdout + reopen_proc.stderr
    reopened_etag = _cli_json(reopen_proc)["value"]["etag"]

    partial_body = _write_file(
        tmp_path / "partial-result.md",
        result_body(
            DEFAULT_PRODUCER_SECTIONS,
            values={"Summary": "Follow-up investigation incomplete."},
        ),
    )
    partial_proc = run_cli(
        project,
        "--actor",
        "implementer",
        "loop",
        "result",
        item_slug,
        "--body-file",
        str(partial_body),
        "--outcome",
        "partial",
        "--model",
        "model-a",
        "--key",
        "res-discovery-partial",
        store=store,
    )
    assert partial_proc.returncode == 0, partial_proc.stdout + partial_proc.stderr

    stale_move_proc = run_cli(
        project,
        "--actor",
        "cto",
        "change",
        "move",
        item_slug,
        "done",
        "--expect",
        reopened_etag,
        store=store,
    )
    assert stale_move_proc.returncode == 1
    stale_move = _cli_json(stale_move_proc)
    assert stale_move["ok"] is False
    assert stale_move["error"]["code"] == "BUSINESS_RULE_VIOLATED"


def test_new_candidate_cannot_reuse_previous_result(tmp_path: Path) -> None:
    project, store = _configure_result_store(tmp_path, with_git=True)

    phase_res = _cli_json(
        run_cli(project, "--actor", "cto", "change", "add", "phase", "Build", store=store)
    )
    phase_slug = phase_res["value"]["slug"]
    item_res = _cli_json(
        run_cli(
            project,
            "--actor",
            "cto",
            "change",
            "add",
            "task",
            "Implement token refresh",
            "--rel",
            f"part_of={phase_slug}",
            store=store,
        )
    )
    item_slug = item_res["value"]["slug"]
    assert (
        run_cli(
            project,
            "--actor",
            "cto",
            "change",
            "move",
            item_slug,
            "doing",
            "--expect",
            item_res["value"]["etag"],
            store=store,
        ).returncode
        == 0
    )

    _add_result_check(store, tmp_path, item_slug, "code-result-check")

    # Candidate A -> Result A -> Check A -> Review A
    (project / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(project, "add", "app.py")
    run_git(project, "commit", "-m", "feat: commit A")
    commit_a = run_git(project, "rev-parse", "HEAD").stdout.strip()

    assert (
        run_cli(
            project,
            "--actor",
            "implementer",
            "loop",
            "candidate",
            item_slug,
            "--commit",
            commit_a,
            "--model",
            "model-a",
            "--key",
            "cand-a",
            store=store,
        ).returncode
        == 0
    )
    body_a = _write_file(
        tmp_path / "result-a.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Implemented A."}),
    )
    res_a = _cli_json(
        run_cli(
            project,
            "--actor",
            "implementer",
            "loop",
            "result",
            item_slug,
            "--body-file",
            str(body_a),
            "--commit",
            commit_a,
            "--outcome",
            "completed",
            "--model",
            "model-a",
            "--key",
            "res-a",
            store=store,
        )
    )
    assert res_a["ok"] is True, res_a
    assert res_a["value"]["candidate_commit"] == commit_a

    assert (
        run_cli(project, "--actor", "cto", "check", "run", item_slug, store=store).returncode
        == 0
    )
    notes_a = _write_file(
        tmp_path / "review-a.md",
        result_body(DEFAULT_REVIEW_SECTIONS, values={"Verdict": "Approve A."}),
    )
    assert (
        run_cli(
            project,
            "--actor",
            "reviewer",
            "loop",
            "review",
            item_slug,
            "--commit",
            commit_a,
            "--verdict",
            "approve",
            "--notes-file",
            str(notes_a),
            "--model",
            "model-b",
            "--key",
            "rev-a",
            store=store,
        ).returncode
        == 0
    )

    # Candidate B supersedes A; A's result/review cannot close B.
    (project / "app.py").write_text("VALUE = 3\n", encoding="utf-8", newline="\n")
    run_git(project, "add", "app.py")
    run_git(project, "commit", "-m", "feat: commit B")
    commit_b = run_git(project, "rev-parse", "HEAD").stdout.strip()

    assert (
        run_cli(
            project,
            "--actor",
            "implementer",
            "loop",
            "candidate",
            item_slug,
            "--commit",
            commit_b,
            "--model",
            "model-a",
            "--key",
            "cand-b",
            store=store,
        ).returncode
        == 0
    )

    etag_before_b = _cli_json(
        run_cli(project, "--actor", "cto", "read", "show", item_slug, store=store)
    )["value"]["doc"]["etag"]
    move_with_old_proc = run_cli(
        project,
        "--actor",
        "cto",
        "change",
        "move",
        item_slug,
        "done",
        "--expect",
        etag_before_b,
        store=store,
    )
    assert move_with_old_proc.returncode == 1
    move_with_old_result = _cli_json(move_with_old_proc)
    assert move_with_old_result["ok"] is False
    assert move_with_old_result["error"]["code"] == "BUSINESS_RULE_VIOLATED"

    # Finish cleanly with Result B -> fresh Check B -> separated Review B -> Done.
    body_b = _write_file(
        tmp_path / "result-b.md",
        result_body(DEFAULT_PRODUCER_SECTIONS, values={"Summary": "Implemented B."}),
    )
    res_b = _cli_json(
        run_cli(
            project,
            "--actor",
            "implementer",
            "loop",
            "result",
            item_slug,
            "--body-file",
            str(body_b),
            "--commit",
            commit_b,
            "--outcome",
            "completed",
            "--model",
            "model-a",
            "--key",
            "res-b",
            store=store,
        )
    )
    assert res_b["ok"] is True, res_b
    assert res_b["value"]["candidate_commit"] == commit_b

    check_b = _cli_json(
        run_cli(project, "--actor", "cto", "check", "run", item_slug, store=store)
    )
    assert check_b["ok"] is True, check_b
    assert check_b["value"]["passed"] is True

    notes_b = _write_file(
        tmp_path / "review-b.md",
        result_body(DEFAULT_REVIEW_SECTIONS, values={"Verdict": "Approve B."}),
    )
    rev_b = _cli_json(
        run_cli(
            project,
            "--actor",
            "reviewer",
            "loop",
            "review",
            item_slug,
            "--commit",
            commit_b,
            "--verdict",
            "approve",
            "--notes-file",
            str(notes_b),
            "--model",
            "model-b",
            "--key",
            "rev-b",
            store=store,
        )
    )
    assert rev_b["ok"] is True, rev_b
    assert rev_b["value"]["result"] == res_b["value"]["record"]
    assert rev_b["value"]["commit"] == commit_b

    done_proc = run_cli(
        project,
        "--actor",
        "cto",
        "change",
        "move",
        item_slug,
        "done",
        "--expect",
        etag_before_b,
        store=store,
    )
    assert done_proc.returncode == 0, done_proc.stdout + done_proc.stderr
    assert _cli_json(done_proc)["value"]["status"] == "done"
