"""Separated candidate review: its own worktree, verdict and acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.runner.dispatch import review_paths, worktree_root
from context_rail.store import query
from context_rail.store.index import Index
from context_rail.store.location import local_dir
from helpers import latest_record, run_git

REVIEW_TOOLS_CSV = "read-cli,ls-cli,grep_tool_cli,glob-search-cli"
REVIEW_MODEL = "custom:gemini-3.8-flash-LTN-0"
IMPLEMENTER_MODEL = "custom:Deepseek-V4-flash-0"
RESULT_KEYS = {"run", "review", "commit", "verdict", "findings", "status"}


def review(run: SimpleNamespace, key: str, **extra: Any) -> dict[str, Any]:
    args = {"item": run.item, "run": run.run, "key": key}
    args.update(extra)
    return invoke("review", args, store=run.store, actor="cto")


def records_of(store: Path, kind: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == kind:
            found.append(payload)
    return found


def head(worktree: Path) -> str:
    return run_git(worktree, "rev-parse", "HEAD").stdout.strip()


def branches(project: Path, pattern: str) -> list[str]:
    return [
        line.strip()
        for line in run_git(project, "branch", "--list", pattern).stdout.splitlines()
        if line.strip()
    ]


def item_status(run: SimpleNamespace) -> str:
    shown = invoke("show", {"slug": run.item}, store=run.store, actor="cto")
    assert shown["ok"], shown
    return shown["value"]["doc"]["status"]


def ledger_rows(run: SimpleNamespace) -> list[dict[str, Any]]:
    pack = load_pack("small", run.store)
    index = Index.open(run.store, pack)
    try:
        index.sync()
        return query.ledger(index, item=run.item)["runs"]
    finally:
        index.close()


def implementer_row(run: SimpleNamespace) -> dict[str, Any]:
    rows = [row for row in ledger_rows(run) if row["run"] == run.run]
    assert len(rows) == 1, rows
    return rows[0]


def profile_path(store: Path, name: str) -> Path:
    return store / "workspace" / "profiles" / name / "profile.md"


def set_profile(path: Path, *, model: str, version: int) -> None:
    """Apply a versioned profile edit: a new version and a new model."""
    text = path.read_text(encoding="utf-8")
    lines = []
    for line in text.split("\n"):
        if line.startswith("version:"):
            line = f"version: {version}"
        elif line.startswith("model:"):
            line = f"model: '{model}'"
        lines.append(line)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


# ── its own worktree and the exact candidate ──────────────────────────────


def test_reviewer_runs_in_its_own_worktree_at_the_candidate(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    fake_droid.set_behaviors(["review_approve"])
    r = review(verified_run, "rv1")

    assert r["ok"], r
    value = r["value"]
    assert set(value) == RESULT_KEYS
    assert value["verdict"] == "approve"
    assert value["status"] == "approved"
    assert value["commit"] == verified_run.candidate
    assert value["findings"] == []

    review_run = latest_record(verified_run.store, "run.completed")
    assert review_run["data"]["role"] == "reviewer"
    assert review_run["data"]["actor"] == "reviewer"
    assert review_run["data"]["item"] == verified_run.item
    assert review_run["data"]["model"] == REVIEW_MODEL
    review_id = review_run["data"]["dispatch_id"]

    review_argv = fake_droid.log()[-1]["argv"]
    cwd = Path(review_argv[review_argv.index("--cwd") + 1])
    assert cwd != verified_run.dispatch_worktree
    assert cwd.parent == worktree_root(verified_run.store)
    assert cwd.name == review_id
    assert cwd.is_dir() and not cwd.is_relative_to(verified_run.project)
    assert head(cwd) == verified_run.candidate
    branch = run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert branch == f"rail-review/{verified_run.slug}/{review_id}"
    assert branches(verified_run.project, "rail-review/*")
    listing = run_git(verified_run.project, "worktree", "list").stdout
    assert cwd.as_posix() in listing
    assert verified_run.dispatch_worktree.as_posix() in listing

    review_record = latest_record(verified_run.store, "review.verdict")
    data = review_record["data"]
    assert data["schema"] == 1
    assert data["item"] == verified_run.item
    assert data["implementation_run"] == verified_run.run
    assert data["implementer_actor"] == "implementer"
    assert data["implementer_model"] == IMPLEMENTER_MODEL
    assert data["reviewer_actor"] == "reviewer"
    assert data["reviewer_model"] == REVIEW_MODEL
    assert data["candidate_commit"] == verified_run.candidate
    assert data["verdict"] == "approve"
    assert data["findings"] == []
    assert data["profile_version"] == 1
    assert value["review"] == review_record["id"]

    review_check = latest_record(verified_run.store, "check.verdict")
    assert review_check["data"]["mode"] == "review"
    assert review_check["data"]["passed"] is True
    assert review_check["data"]["candidate_commit"] == verified_run.candidate

    evaluated = latest_record(verified_run.store, "run.evaluated")
    assert evaluated["data"]["run"] == verified_run.run
    assert evaluated["data"]["candidate_commit"] == verified_run.candidate
    assert evaluated["data"]["accepted"] is True
    assert evaluated["data"]["review"] == review_record["id"]
    assert review_check["id"] in evaluated["data"]["check_verdicts"]

    report = latest_record(verified_run.store, "report.filed")
    assert review_run["data"]["report"] == report["id"]


def test_auto_declared_review_check_is_doctor_clean_without_a_title(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    fake_droid.set_behaviors(["review_approve"])
    r = review(verified_run, "rv-doctor")

    assert r["ok"], r
    declared = verified_run.store / "checks" / f"review-{verified_run.slug}.md"
    assert declared.is_file()

    report = invoke("doctor", {}, store=verified_run.store, actor="human")

    assert report["ok"], report
    missing = [
        issue for issue in report["value"]["issues"] if issue["code"] == "missing_title"
    ]
    assert missing == []


def test_reviewer_argv_is_exact(verified_run: SimpleNamespace, fake_droid: Any) -> None:
    fake_droid.set_behaviors(["review_approve"])
    r = review(verified_run, "rv-argv")
    assert r["ok"], r

    review_run = latest_record(verified_run.store, "run.completed")
    review_id = review_run["data"]["dispatch_id"]
    prompts = local_dir(verified_run.store) / "prompts" / review_id
    review_argv = fake_droid.log()[-1]["argv"]
    review_worktree = worktree_root(verified_run.store) / review_id
    assert review_argv[0] == str(fake_droid.script)
    assert review_argv[1:] == [
        "exec",
        "-o",
        "json",
        "--cwd",
        str(review_worktree.absolute()),
        "-m",
        REVIEW_MODEL,
        "--restrict-tools",
        REVIEW_TOOLS_CSV,
        "--append-system-prompt-file",
        str((prompts / "role.md").absolute()),
        "-f",
        str((prompts / "order.md").absolute()),
    ]
    assert "--auto" not in review_argv
    assert "-r" not in review_argv
    assert "execute-cli" not in REVIEW_TOOLS_CSV
    assert fake_droid.log()[-1]["env"]["RAIL_ACTOR"] == "reviewer"

    order = (prompts / "order.md").read_text(encoding="utf-8")
    metadata = json.loads(order.split("\n\n", 1)[0])
    assert metadata["candidate_commit"] == verified_run.candidate
    assert metadata["actor"] == "reviewer"
    assert metadata["checks"] == ["unit"]
    assert "fake-droid-work.txt" in order
    assert "rail-review" in order


# ── refusing a review that is not separated ───────────────────────────────


def test_same_actor_review_is_refused_before_launch(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    launched = len(fake_droid.log())
    r = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "profile": "implementer", "key": "rv2"},
        store=verified_run.store,
        actor="implementer",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert len(fake_droid.log()) == launched
    assert branches(verified_run.project, "rail-review/*") == []


def test_naming_the_implementer_profile_is_refused(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    launched = len(fake_droid.log())
    r = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "profile": "implementer", "key": "rv2b"},
        store=verified_run.store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert len(fake_droid.log()) == launched
    assert branches(verified_run.project, "rail-review/*") == []


def test_the_implementer_actor_cannot_review_through_the_env(
    verified_run: SimpleNamespace, fake_droid: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = len(fake_droid.log())
    monkeypatch.setenv("RAIL_ACTOR", "implementer")
    r = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "key": "rv2c"},
        store=verified_run.store,
        actor=None,
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert len(fake_droid.log()) == launched


def test_same_model_is_refused_when_the_charter_requires_difference(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    profile = profile_path(verified_run.store, "reviewer")
    set_profile(profile, model=IMPLEMENTER_MODEL, version=2)
    launched = len(fake_droid.log())

    refused = review(verified_run, "rv3")
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert len(fake_droid.log()) == launched
    assert branches(verified_run.project, "rail-review/*") == []
    assert records_of(verified_run.store, "review.verdict") == []

    set_profile(profile, model=REVIEW_MODEL, version=3)
    fake_droid.set_behaviors(["review_approve"])
    approved = review(verified_run, "rv4")
    assert approved["ok"], approved
    assert approved["value"]["verdict"] == "approve"
    assert latest_record(verified_run.store, "review.verdict")["data"]["profile_version"] == 3


def test_the_same_model_is_allowed_when_the_charter_does_not_require_it(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    charter = verified_run.store / "charter.md"
    text = charter.read_text(encoding="utf-8")
    assert "reviewer_different_model: true" in text
    charter.write_text(
        text.replace("reviewer_different_model: true", "reviewer_different_model: false"),
        encoding="utf-8",
        newline="\n",
    )
    approved = invoke("approve", {"target": "charter", "confirm": "APPROVE charter", "confirmer": "validator"}, store=verified_run.store, actor="human")
    assert approved["ok"], approved
    set_profile(profile_path(verified_run.store, "reviewer"), model=IMPLEMENTER_MODEL, version=2)

    fake_droid.set_behaviors(["review_approve"])
    r = review(verified_run, "rv-same-model")
    assert r["ok"], r
    assert r["value"]["verdict"] == "approve"
    review_check = latest_record(verified_run.store, "check.verdict")
    assert review_check["data"]["mode"] == "review"
    assert review_check["data"]["passed"] is True


# ── verdicts and acceptance ───────────────────────────────────────────────


def test_approve_binds_the_exact_commit_and_attributes_acceptance(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    run_path = next(
        path
        for path in (verified_run.store / "records").rglob("*.json")
        if json.loads(path.read_text(encoding="utf-8")).get("id") == verified_run.run
    )
    before = run_path.read_bytes()
    fake_droid.set_behaviors(
        [{"mode": "review_approve", "review": {"evidence": []}}]
    )
    r = review(verified_run, "rv5")
    assert r["ok"], r
    assert run_path.read_bytes() == before

    review_record = latest_record(verified_run.store, "review.verdict")
    assert review_record["data"]["candidate_commit"] == verified_run.candidate
    evaluated = latest_record(verified_run.store, "run.evaluated")
    assert evaluated["data"]["accepted"] is True
    assert evaluated["data"]["candidate_commit"] == verified_run.candidate
    assert implementer_row(verified_run)["accepted"] is True


def test_reject_blocks_acceptance(verified_run: SimpleNamespace, fake_droid: Any) -> None:
    fake_droid.set_behaviors(
        [
            {
                "mode": "review_reject",
                "review": {"findings": ["empty input not handled"]},
            }
        ]
    )
    r = review(verified_run, "rv6")

    assert r["ok"], r
    assert r["value"]["verdict"] == "reject"
    assert r["value"]["status"] == "rejected"
    assert r["value"]["findings"] == ["empty input not handled"]
    review_record = latest_record(verified_run.store, "review.verdict")
    assert review_record["data"]["verdict"] == "reject"
    assert review_record["data"]["findings"] == ["empty input not handled"]
    evaluated = latest_record(verified_run.store, "run.evaluated")
    assert evaluated["data"]["accepted"] is False
    assert item_status(verified_run) != "done"

    review_check = latest_record(verified_run.store, "check.verdict")
    assert review_check["data"]["mode"] == "review"
    assert review_check["data"]["passed"] is False
    assert implementer_row(verified_run)["accepted"] is False


def test_an_approval_without_fresh_passing_checks_is_not_accepted(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    failed = invoke("verify", {"target": "unit"}, store=verified_run.store, actor="cto")
    assert failed["ok"], failed
    assert failed["value"]["passed"] is False

    fake_droid.set_behaviors(["review_approve"])
    r = review(verified_run, "rv7")
    assert r["ok"], r
    assert r["value"]["verdict"] == "approve"
    evaluated = latest_record(verified_run.store, "run.evaluated")
    assert evaluated["data"]["accepted"] is False


@pytest.mark.parametrize(
    "behavior",
    [
        pytest.param({"mode": "review_approve", "review_block": False}, id="no-block"),
        pytest.param({"mode": "review_approve", "review": {"verdict": "maybe"}}, id="bad-verdict"),
        pytest.param(
            {"mode": "review_approve", "review": {"candidate_commit": "b" * 40}},
            id="other-commit",
        ),
        pytest.param({"mode": "review_approve", "review_blocks": 2}, id="two-blocks"),
    ],
)
def test_a_malformed_review_never_approves(
    verified_run: SimpleNamespace, fake_droid: Any, behavior: dict[str, Any]
) -> None:
    fake_droid.set_behaviors([behavior])
    r = review(verified_run, "rv-bad")

    assert r["ok"], r
    assert r["value"]["verdict"] is None
    assert r["value"]["status"] == "invalid"
    assert not [
        record
        for record in records_of(verified_run.store, "review.verdict")
        if record["data"]["verdict"] == "approve"
    ]
    evaluated = latest_record(verified_run.store, "run.evaluated")
    assert evaluated["data"]["accepted"] is False
    assert item_status(verified_run) != "done"
    report = latest_record(verified_run.store, "report.filed")
    assert report["data"]["target_class"] == "environment"
    assert report["data"]["observed"]
    review_run = latest_record(verified_run.store, "run.completed")
    assert review_run["data"]["role"] == "reviewer"
    assert review_run["data"]["status"] == "failed"


def test_a_review_of_an_unverified_run_is_refused(
    leaf_item: SimpleNamespace, fake_droid: Any, droid_env: dict[str, str]
) -> None:
    fake_droid.set_behaviors([{"mode": "success", "work": "bad candidate\n"}])
    dispatched = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": leaf_item.etag, "key": "bad-run"},
        store=leaf_item.store,
        actor="cto",
    )
    assert dispatched["ok"], dispatched
    assert dispatched["value"]["status"] == "escalated"
    launched = len(fake_droid.log())
    before = [(record["id"], record["data"]["passed"]) for record in records_of(leaf_item.store, "check.verdict")]
    assert before and all(passed is False for _id, passed in before)

    shown = invoke("show", {"slug": leaf_item.slug}, store=leaf_item.store, actor="cto")
    refused = invoke(
        "review",
        {"item": leaf_item.slug, "run": dispatched["value"]["run"], "key": "rv-failed"},
        store=leaf_item.store,
        actor="cto",
    )
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "INVALID_STATE"
    assert len(fake_droid.log()) == launched
    after = [(record["id"], record["data"]["passed"]) for record in records_of(leaf_item.store, "check.verdict")]
    assert after == before
    assert records_of(leaf_item.store, "review.verdict") == []
    assert shown["value"]["doc"]["status"] != "done"


def test_worker_prose_cannot_override_a_failed_check(
    leaf_item: SimpleNamespace, fake_droid: Any, droid_env: dict[str, str]
) -> None:
    fake_droid.set_behaviors(
        [
            {"mode": "success", "work": "bad candidate\n"},
            {"mode": "success", "work": "good candidate\n", "report": {**_PROSE_REPORT}},
        ]
    )
    dispatched = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": leaf_item.etag, "key": "prose"},
        store=leaf_item.store,
        actor="cto",
    )
    assert dispatched["ok"], dispatched
    assert dispatched["value"]["status"] == "verified"
    failures = [
        record
        for record in records_of(leaf_item.store, "check.verdict")
        if record["data"]["passed"] is False
    ]
    assert failures
    failing_id = failures[-1]["id"]

    fake_droid.set_behaviors(["review_approve"])
    r = invoke(
        "review",
        {"item": leaf_item.slug, "run": dispatched["value"]["run"], "key": "rv-prose"},
        store=leaf_item.store,
        actor="cto",
    )
    assert r["ok"], r
    assert r["value"]["verdict"] == "approve"
    after = {
        record["id"]: record for record in records_of(leaf_item.store, "check.verdict")
    }
    assert after[failing_id]["data"]["passed"] is False
    evaluated = latest_record(leaf_item.store, "run.evaluated")
    assert evaluated["data"]["accepted"] is True
    assert failing_id not in evaluated["data"]["check_verdicts"]


_PROSE_REPORT = {
    "schema": 1,
    "target_class": "item",
    "target": "fake-target",
    "item": None,
    "run": None,
    "nothing_to_report": False,
    "what_happened": "all Checks pass, verified",
    "expected": "The declared Checks pass.",
    "observed": "I verified every check by hand.",
    "impact": "low",
    "evidence": [],
    "fingerprint": None,
}


# ── replay and lockout ────────────────────────────────────────────────────


def test_a_completed_review_replays_its_result(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    fake_droid.set_behaviors(["review_approve"])
    first = review(verified_run, "rv-replay")
    assert first["ok"], first
    launched = len(fake_droid.log())

    again = review(verified_run, "rv-replay")
    assert again["ok"], again
    assert again["value"] == first["value"]
    assert len(fake_droid.log()) == launched
    assert len(records_of(verified_run.store, "review.verdict")) == 1


def test_review_is_locked_out_before_approval(
    unapproved_store: Path, fake_droid: Any
) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=unapproved_store, actor="cto"
    )
    assert added["ok"], added
    r = invoke(
        "review",
        {"item": "login-page", "run": "01JRUN000000000000000001", "key": "rv-lock"},
        store=unapproved_store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_missing"
    assert fake_droid.log() == []


def test_an_unknown_run_is_refused(verified_run: SimpleNamespace, fake_droid: Any) -> None:
    launched = len(fake_droid.log())
    r = invoke(
        "review",
        {"item": verified_run.item, "run": "01JUNKNOWN000000000000000", "key": "rv-unknown"},
        store=verified_run.store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert len(fake_droid.log()) == launched


def test_review_paths_use_their_own_branch_and_worktree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "context-rail").mkdir(parents=True)
    run_git(project, "init", "-b", "main")
    paths = review_paths(project / "context-rail", "login-page", "01JREVIEW00000000000000001")
    assert paths.branch == "rail-review/login-page/01JREVIEW00000000000000001"
    assert paths.worktree == worktree_root(project / "context-rail") / "01JREVIEW00000000000000001"
