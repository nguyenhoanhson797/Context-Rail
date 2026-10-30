"""Isolated implementer worktrees, attempts, records and escalation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.store.location import local_dir
from helpers import item_etag, latest_record, process_alive, run_cli, run_git, start_cli

RESULT_KEYS = {
    "dispatch_id",
    "run",
    "branch",
    "worktree",
    "attempts",
    "verdicts",
    "status",
    "escalation",
}
TOOLS_CSV = (
    "read-cli,ls-cli,grep_tool_cli,glob-search-cli,create-cli,edit-cli,execute-cli"
)


def dispatch(leaf: SimpleNamespace, key: str, **extra: Any) -> dict[str, Any]:
    args = {"item": leaf.slug, "expect": leaf.etag, "key": key}
    args.update(extra)
    return invoke("dispatch", args, store=leaf.store, actor="cto")


def records_of(store: Path, kind: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == kind:
            found.append(payload)
    return found


def progress_files(store: Path) -> list[dict[str, Any]]:
    folder = local_dir(store) / "dispatch"
    if not folder.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return entries


def head(worktree: Path, revision: str = "HEAD") -> str:
    return run_git(worktree, "rev-parse", revision).stdout.strip()


def branches(worktree: Path, pattern: str) -> list[str]:
    return [
        line.strip()
        for line in run_git(worktree, "branch", "--list", pattern).stdout.splitlines()
        if line.strip()
    ]


def worktrees(worktree: Path) -> list[str]:
    return run_git(worktree, "worktree", "list", "--porcelain").stdout.splitlines()


def status(leaf: SimpleNamespace) -> str:
    shown = invoke("show", {"slug": leaf.slug}, store=leaf.store, actor="cto")
    assert shown["ok"], shown
    return shown["value"]["doc"]["status"]


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill a started CLI and every process it launched."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()


def wait_for(predicate: Any, *, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("the expected state never appeared")


# ── success ───────────────────────────────────────────────────────────────


def test_success_creates_a_verified_candidate(leaf_item, fake_droid, droid_env) -> None:
    base = head(leaf_item.root)
    r = dispatch(leaf_item, "d1")

    assert r["ok"], r
    value = r["value"]
    assert set(value) == RESULT_KEYS
    assert value["status"] == "verified"
    assert value["attempts"] == 1
    assert value["escalation"] is None
    assert value["branch"] == f"rail/{leaf_item.slug}/{value['dispatch_id']}"
    worktree = Path(value["worktree"])
    assert worktree.is_dir()
    root = Path(tempfile.gettempdir()) / "context-rail-worktrees"
    assert worktree.is_relative_to(root)
    assert worktree.name == value["dispatch_id"]
    assert not worktree.is_relative_to(leaf_item.root)

    registered = progress_files(leaf_item.store)
    assert [entry["worktree"] for entry in registered] == [str(worktree)]
    assert registered[0]["branch"] == value["branch"]

    assert branches(leaf_item.root, "rail/*")
    assert any(value["branch"] in line for line in branches(leaf_item.root, "rail/*"))
    candidate = head(worktree)
    assert candidate != base

    assert (leaf_item.root / "src" / "app.py").read_bytes() == b"VALUE = 1\n"
    assert head(leaf_item.root) != candidate
    assert run_git(leaf_item.root, "show", "main:src/app.py").stdout == "VALUE = 1\n"
    assert any(value["dispatch_id"] in line for line in worktrees(leaf_item.root))

    run_record = latest_record(leaf_item.store, "run.completed")
    data = run_record["data"]
    assert data["item"] == leaf_item.slug
    assert data["role"] == "implementer"
    assert data["actor"] == "implementer"
    assert data["profile"] == "implementer"
    assert data["profile_version"] == 1
    assert data["status"] == "verified"
    assert len(data["attempts"]) == 1
    assert data["candidate_commit"] == candidate
    assert data["accepted"] is None
    assert data["skills"] and data["skills"][0]["version"] >= 1
    assert data["capture"] == {"complete": True, "reason": None, "omitted": 0}
    assert "fake-droid-work.txt" in data["files_changed"]
    assert data["commits_changed"] == [candidate]
    assert value["run"] == run_record["id"]
    assert value["verdicts"] == data["verdicts"]

    verdict = latest_record(leaf_item.store, "check.verdict")
    assert verdict["data"]["passed"] is True
    assert verdict["about"] == ["unit", leaf_item.slug]


def test_implementer_argv_is_exact(leaf_item, fake_droid, droid_env) -> None:
    r = dispatch(leaf_item, "argv")
    assert r["ok"], r
    worktree = Path(r["value"]["worktree"])
    prompts = local_dir(leaf_item.store) / "prompts" / r["value"]["dispatch_id"]
    argv = fake_droid.log()[0]["argv"]
    assert argv[0] == str(fake_droid.script)
    assert argv[1:] == [
        "exec",
        "-o",
        "json",
        "--cwd",
        str(worktree),
        "-m",
        "custom:Deepseek-V4-flash-0",
        "--restrict-tools",
        TOOLS_CSV,
        "--append-system-prompt-file",
        str(prompts / "role.md"),
        "-f",
        str(prompts / "order.md"),
        "--auto",
        "medium",
    ]
    assert "-r" not in argv
    assert "--enabled-tools" not in argv
    assert "high" not in argv


def test_work_order_names_the_canonical_store_and_the_item(leaf_item, fake_droid, droid_env) -> None:
    r = dispatch(leaf_item, "order")
    assert r["ok"], r
    prompt = fake_droid.log()[0]["prompt"]
    assert "login-page" in prompt
    assert str(leaf_item.store) in prompt
    assert "no-store-edit rule" in prompt
    assert "rail-report" in prompt
    assert "--store" in prompt


# ── retries and escalation ────────────────────────────────────────────────


def test_retry_continues_the_same_session(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(
        [
            {"mode": "success", "work": "bad candidate\n", "stderr": "unit check exploded"},
            {"mode": "success", "work": "good candidate\n"},
        ]
    )
    r = dispatch(leaf_item, "d2")

    assert r["ok"], r
    assert r["value"]["status"] == "verified"
    assert r["value"]["attempts"] == 2
    log = fake_droid.log()
    assert len(log) == 2
    first, second = log
    session = fake_droid.session_ids()[0]
    assert "-s" not in first["argv"]
    assert second["argv"][second["argv"].index("-s") + 1] == session
    assert second["argv"][second["argv"].index("--cwd") + 1] == first["argv"][
        first["argv"].index("--cwd") + 1
    ]
    assert second["argv"][second["argv"].index("-m") + 1] == first["argv"][
        first["argv"].index("-m") + 1
    ]
    assert second["argv"][second["argv"].index("--restrict-tools") + 1] == TOOLS_CSV
    assert second["argv"][second["argv"].index("--auto") + 1] == "medium"
    assert "unit" in second["prompt"] and "unit check exploded" in second["prompt"]

    run = latest_record(leaf_item.store, "run.completed")
    attempts = run["data"]["attempts"]
    assert [entry["status"] for entry in attempts] == ["failed", "verified"]
    assert attempts[0]["session_id"] == session
    assert attempts[1]["session_id"] == session
    verdicts = [json.loads(path.read_text(encoding="utf-8")) for path in (leaf_item.store / "records").rglob("*.json")]
    checks = [row for row in verdicts if row.get("kind") == "check.verdict"]
    assert [row["data"]["passed"] for row in checks] == [False, True]
    assert len(branches(leaf_item.root, f"rail/{leaf_item.slug}/*")) == 1


def test_two_failures_escalate_and_block(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors([{"mode": "success", "work": "bad candidate\n"}])
    r = dispatch(leaf_item, "d3")

    assert r["ok"], r
    assert r["value"]["status"] == "escalated"
    assert r["value"]["escalation"]
    assert r["value"]["attempts"] == 2
    assert status(leaf_item) == "blocked"

    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["status"] == "failed"
    assert [entry["status"] for entry in run["data"]["attempts"]] == ["failed", "failed"]
    assert run["data"]["accepted"] is not True
    escalation = latest_record(leaf_item.store, "dispatch.escalated")
    assert escalation["data"]["run"] == run["id"]
    assert escalation["data"]["worktree"] == r["value"]["worktree"]
    assert escalation["data"]["branch"] == r["value"]["branch"]
    assert Path(r["value"]["worktree"]).is_dir()
    assert branches(leaf_item.root, f"rail/{leaf_item.slug}/*")
    checks = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (leaf_item.store / "records").rglob("*.json")
    ]
    failures = [row for row in checks if row.get("kind") == "check.verdict"]
    assert failures and all(row["data"]["passed"] is False for row in failures)
    assert run["data"]["verdicts"] == [failures[-1]["id"]]


# ── refusals ──────────────────────────────────────────────────────────────


def test_dispatch_refused_before_approval(unapproved_store, fake_droid, tmp_path) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=unapproved_store, actor="cto"
    )
    assert added["ok"], added
    shown = invoke("show", {"slug": "login-page"}, store=unapproved_store, actor="cto")
    etag = shown["value"]["doc"]["etag"]
    r = invoke(
        "dispatch",
        {"item": "login-page", "expect": etag, "key": "k-d1"},
        store=unapproved_store,
        actor="cto",
    )
    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_missing"
    assert fake_droid.log() == []
    assert shown["value"]["doc"]["status"] == "todo"
    assert not (local_dir(unapproved_store) / "dispatch").exists()


def test_dispatch_refused_after_a_protected_edit(leaf_item, fake_droid, droid_env) -> None:
    charter = leaf_item.store / "charter.md"
    charter.write_text(charter.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")
    r = dispatch(leaf_item, "k-d2")
    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_mismatch"
    assert fake_droid.log() == []


def test_budget_spent_refused(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["malformed_json"])
    first = dispatch(leaf_item, "spend")
    assert first["ok"], first
    assert first["value"]["attempts"] == 2
    launched = len(fake_droid.log())

    shown = invoke("show", {"slug": leaf_item.slug}, store=leaf_item.store, actor="cto")
    etag = shown["value"]["doc"]["etag"]
    for target in ("doing", "todo"):
        moved = invoke(
            "move", {"slug": leaf_item.slug, "status": target, "expect": etag},
            store=leaf_item.store,
            actor="cto",
        )
        assert moved["ok"], moved
        etag = invoke(
            "show", {"slug": leaf_item.slug}, store=leaf_item.store, actor="cto"
        )["value"]["doc"]["etag"]

    r = dispatch(leaf_item, "k-d8", expect=etag)
    assert r["ok"] is False, r
    assert r["error"]["code"] in ("BUSINESS_RULE_VIOLATED", "INVALID_STATE"), r
    assert len(fake_droid.log()) == launched


def test_a_fresh_dispatch_launches_after_an_approving_review(
    verified_run, fake_droid, droid_env
) -> None:
    """VAL-DISPATCH-008 refused on two spent *implementer* attempts, not on a review."""
    fake_droid.set_behaviors(["review_approve"])
    reviewed = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "key": "after-approve-r"},
        store=verified_run.store,
        actor="cto",
    )
    assert reviewed["ok"], reviewed
    launched = len(fake_droid.log())

    fake_droid.set_behaviors(["success"])
    etag = item_etag(verified_run)
    again = invoke(
        "dispatch",
        {"item": verified_run.item, "expect": etag, "key": "after-approve-d"},
        store=verified_run.store,
        actor="cto",
    )

    assert again["ok"], again
    assert again["value"]["dispatch_id"] != verified_run.dispatch_id
    assert again["value"]["status"] == "verified"
    assert len(fake_droid.log()) == launched + 1


def test_a_fresh_dispatch_launches_after_a_rejecting_review(
    rejected_run, fake_droid, droid_env
) -> None:
    """A rejection is the approved Stage 1b re-dispatch loop; attempts stay open."""
    launched = len(fake_droid.log())

    fake_droid.set_behaviors(["success"])
    again = invoke(
        "dispatch",
        {"item": rejected_run.item, "expect": item_etag(rejected_run), "key": "after-reject-d"},
        store=rejected_run.store,
        actor="cto",
    )

    assert again["ok"], again
    assert again["value"]["status"] == "verified"
    assert again["value"]["dispatch_id"] != rejected_run.dispatch_id
    assert len(fake_droid.log()) == launched + 1


def test_worker_store_edits_are_refused(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors([{"mode": "success", "store_edit": True}])
    r = dispatch(leaf_item, "store")
    assert r["ok"], r
    assert r["value"]["status"] == "escalated"
    assert r["value"]["verdicts"] == []
    assert not (leaf_item.store / "worker-note.txt").exists()
    report = latest_record(leaf_item.store, "report.filed")
    assert report["data"]["target_class"] == "environment"
    assert "context-rail/worker-note.txt" in report["data"]["observed"]
    run = latest_record(leaf_item.store, "run.completed")
    assert "context-rail/worker-note.txt" not in run["data"]["files_changed"]
    assert (leaf_item.root / "src" / "app.py").read_bytes() == b"VALUE = 1\n"


def _fake_git(
    real: Any, scripted: dict[str, list[dict[str, Any] | None]]
) -> tuple[Any, list[list[str]]]:
    """Replace ``git``'s first subcommand with a scripted sequence per command.

    A ``None`` step means "run the real command", so a test can fail one call
    and let the next one behave normally.
    """
    seen: list[list[str]] = []

    def runner(args: list[str], cwd: Path, timeout: float = 120.0) -> dict[str, Any]:
        seen.append(list(args))
        queue = scripted.get(args[0]) if args else None
        if queue:
            step = queue.pop(0) if len(queue) > 1 else queue[0]
            if step is not None:
                return step
        return real(args, cwd=cwd, timeout=timeout)

    return runner, seen


def _git_failure() -> dict[str, Any]:
    return {
        "exit": 1,
        "timed_out": False,
        "duration_s": 0.01,
        "stdout": "",
        "stderr": "The following paths are ignored by one of your .gitignore files",
        "omitted_bytes": 0,
        "digest": "sha256:0000",
    }


def test_a_failed_checkpoint_add_forces_the_paths_then_refuses(
    leaf_item, fake_droid, droid_env
) -> None:
    """A global excludesfile must never make the checkpoint silently drop paths."""
    import context_rail.gateway.handlers.dispatch as dispatch_module

    agents = leaf_item.root / ".agents" / "skills" / "hint" / "SKILL.md"
    agents.parent.mkdir(parents=True, exist_ok=True)
    agents.write_text("# hint\n", encoding="utf-8", newline="\n")
    real = dispatch_module._git
    runner, seen = _fake_git(real, {"add": [_git_failure(), None]})
    dispatch_module._git = runner
    try:
        forced = dispatch(leaf_item, "checkpoint-forced")
    finally:
        dispatch_module._git = real

    adds = [call for call in seen if call[:1] == ["add"]]
    assert any("--force" in call for call in adds), adds
    assert forced["ok"], forced
    staged = run_git(leaf_item.root, "ls-files", ".agents").stdout.strip()
    assert staged, "the checkpoint left .agents untracked"


def test_a_checkpoint_that_cannot_add_a_path_refuses_instead_of_reporting_success(
    leaf_item, fake_droid, droid_env
) -> None:
    """When even the forced add fails, the checkpoint must not claim success."""
    import context_rail.gateway.handlers.dispatch as dispatch_module

    real = dispatch_module._git
    runner, seen = _fake_git(real, {"add": [_git_failure(), _git_failure()]})
    dispatch_module._git = runner
    try:
        refused = dispatch(leaf_item, "checkpoint-refused")
    finally:
        dispatch_module._git = real

    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "PROCESS_ERROR", refused
    assert len([call for call in seen if call[:1] == ["add"]]) == 2


def test_a_store_edit_in_the_dropped_middle_is_still_refused(
    leaf_item, fake_droid, droid_env
) -> None:
    """A truncated diff must not hide the store path a worker committed."""
    fake_droid.set_behaviors([{"mode": "success", "store_edit": True, "bulk": 200}])
    r = dispatch(leaf_item, "store-middle")
    assert r["ok"], r
    assert r["value"]["status"] == "escalated", r
    report = latest_record(leaf_item.store, "report.filed")
    assert "context-rail/worker-note.txt" in report["data"]["observed"]
    run = latest_record(leaf_item.store, "run.completed")
    capture = run["data"]["capture"]
    assert capture["complete"] is False
    assert capture["omitted"] > 0
    assert not (leaf_item.store / "worker-note.txt").exists()


def test_a_store_at_the_project_root_still_refuses_a_worker_edit(
    tmp_path: Path, picture_answers: dict[str, Any], fake_droid: Any, droid_env
) -> None:
    """``--root .`` makes the project root the store; a store edit is still an edit."""
    project = tmp_path / "rooted"
    project.mkdir()
    answers = tmp_path / "rooted-answers.json"
    answers.write_text(json.dumps(picture_answers, indent=2), encoding="utf-8")
    started = invoke(
        "init",
        {
            "dir": str(project),
            "root": ".",
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    assert started["ok"], started
    store = project
    from helpers import approve_picture

    approved = approve_picture(store)
    assert approved["ok"], approved
    added = invoke(
        "add",
        {"preset": "task", "title": "Login page", "rel": ["part_of=send-invoice"]},
        store=store,
        actor="cto",
    )
    assert added["ok"], added
    spec = tmp_path / "rooted-unit.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "cmd",
                "acceptance": False,
                "cmd": [sys.executable, "-c", "print('ok')"],
                "inputs": ["src/*.py"],
                "timeout_s": 60,
            }
        ),
        encoding="utf-8",
    )
    declared = invoke(
        "check",
        {"slug": "unit", "target": "login-page", "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )
    assert declared["ok"], declared
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (project / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    run_git(project, "init", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", "picture and leaf task")
    shown = invoke("show", {"slug": "login-page"}, store=store, actor="cto")
    assert shown["ok"], shown

    fake_droid.set_behaviors(
        [{"mode": "success", "store_path": "worker-note.txt"}]
    )
    r = invoke(
        "dispatch",
        {
            "item": "login-page",
            "expect": shown["value"]["doc"]["etag"],
            "key": "rooted-store-edit",
        },
        store=store,
        actor="cto",
    )

    assert r["ok"], r
    assert r["value"]["status"] == "escalated", r
    report = latest_record(store, "report.filed")
    assert "worker-note.txt" in report["data"]["observed"]
    assert not (store / "worker-note.txt").exists()


@pytest.mark.timeout(120)
def test_pending_launch_refuses_relaunch(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["timeout"])
    proc = start_cli(
        leaf_item.root,
        "dispatch",
        leaf_item.slug,
        "--expect",
        leaf_item.etag,
        "--key",
        "k-d7",
        store=leaf_item.store,
    )
    try:
        wait_for(lambda: bool(fake_droid.log()))
        wait_for(
            lambda: any(
                entry.get("phase") in ("running", "worktree", "started")
                for entry in progress_files(leaf_item.store)
            )
        )
        pending = [
            entry
            for entry in progress_files(leaf_item.store)
            if entry.get("phase") != "completed"
        ]
        assert pending, progress_files(leaf_item.store)
        dispatch_id = pending[0]["dispatch_id"]
        launched = len(fake_droid.log())
    finally:
        kill_tree(proc)

    child = fake_droid.root / "fake-droid-child.json"
    if child.is_file():
        pid = json.loads(child.read_text(encoding="utf-8"))["pid"]
        wait_for(lambda: not process_alive(pid))

    second = run_cli(
        leaf_item.root,
        "dispatch",
        leaf_item.slug,
        "--expect",
        leaf_item.etag,
        "--key",
        "k-d7",
        store=leaf_item.store,
    )
    payload = json.loads(second.stdout)
    assert payload["ok"] is False, payload
    assert payload["error"]["code"] == "CONFLICT"
    details = payload["error"]["details"]
    assert details["dispatch_id"] == dispatch_id
    assert details["phase"]
    assert details["worktree"]
    assert len(fake_droid.log()) == launched
    worktree_entries = [
        entry for entry in progress_files(leaf_item.store) if entry.get("worktree")
    ]
    assert len(worktree_entries) == 1


# ── droid protocol outcomes ───────────────────────────────────────────────


def test_missing_usage_is_stored_as_unknown(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["missing_usage"])
    r = dispatch(leaf_item, "u1")
    assert r["ok"], r
    run = latest_record(leaf_item.store, "run.completed")
    usage = run["data"]["usage"]
    attempts = len(run["data"]["attempts"])
    assert attempts >= 1
    for counter, total in usage["known_sum"].items():
        assert total == 0
        assert usage["unknown_attempts"][counter] == attempts
    assert run["data"]["attempts"][0]["usage"]["input_tokens"] == "unknown"


def test_reported_usage_keeps_zeros(leaf_item, fake_droid, droid_env) -> None:
    r = dispatch(leaf_item, "u2")
    assert r["ok"], r
    usage = latest_record(leaf_item.store, "run.completed")["data"]["usage"]
    assert usage["known_sum"] == {
        "input_tokens": 120,
        "output_tokens": 40,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "factory_credits": 0.25,
    }
    assert usage["unknown_attempts"] == dict.fromkeys(usage["known_sum"], 0)


def test_malformed_usage_fails_the_attempt_and_files_a_report(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["malformed_usage"])
    r = dispatch(leaf_item, "u3")
    assert r["ok"], r
    assert r["value"]["status"] == "escalated"
    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["status"] == "failed"
    attempt = run["data"]["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["usage"]["output_tokens"] == 2
    assert attempt["usage"]["input_tokens"] == "unknown"
    assert attempt["usage"]["cache_read_input_tokens"] == "unknown"
    assert attempt["usage"]["cache_creation_input_tokens"] == "unknown"
    assert attempt["usage"]["factory_credits"] == "unknown"
    report = latest_record(leaf_item.store, "report.filed")
    assert report["data"]["target_class"] == "environment"
    assert "malformed usage" in report["data"]["observed"]
    escalation = latest_record(leaf_item.store, "dispatch.escalated")
    assert "malformed usage" in escalation["data"]["reason"]


@pytest.mark.parametrize(
    ("mode", "needle"),
    [
        ("nonzero_exit", "exited 3"),
        ("is_error", "is_error"),
        ("truncated_json", "not a single result"),
    ],
)
def test_failed_executions_never_verify(leaf_item, fake_droid, droid_env, mode, needle) -> None:
    fake_droid.set_behaviors([mode])
    r = dispatch(leaf_item, f"fail-{mode}")
    assert r["ok"], r
    assert r["value"]["status"] != "verified"
    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["status"] == "failed"
    assert run["data"]["accepted"] is not True
    escalation = latest_record(leaf_item.store, "dispatch.escalated")
    assert needle in escalation["data"]["reason"], escalation["data"]["reason"]


@pytest.mark.timeout(120)
def test_hung_droid_is_killed_with_its_process_tree(leaf_item, fake_droid, droid_env) -> None:
    profile = leaf_item.store / "workspace" / "profiles" / "implementer" / "profile.md"
    text = profile.read_text(encoding="utf-8")
    assert "seconds: 3600" in text
    profile.write_text(text.replace("seconds: 3600", "seconds: 10"), encoding="utf-8")
    fake_droid.set_behaviors(["timeout"])
    started = time.monotonic()
    r = dispatch(leaf_item, "hung")
    elapsed = time.monotonic() - started

    assert r["ok"], r
    assert elapsed < 60
    assert r["value"]["status"] != "verified"
    run = latest_record(leaf_item.store, "run.completed")
    attempt = run["data"]["attempts"][0]
    assert attempt["timed_out"] is True
    assert attempt["status"] == "failed"
    assert Path(r["value"]["worktree"]).is_dir()
    assert branches(leaf_item.root, f"rail/{leaf_item.slug}/*")
    child = fake_droid.root / "fake-droid-child.json"
    assert child.is_file()
    pid = json.loads(child.read_text(encoding="utf-8"))["pid"]
    wait_for(lambda: not process_alive(pid), timeout=30)


@pytest.mark.timeout(120)
def test_output_over_the_cap_is_not_partial_success(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["large_output"])
    r = dispatch(leaf_item, "cap")
    assert r["ok"], r
    assert r["value"]["status"] != "verified"
    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["status"] == "failed"
    capture = run["data"]["capture"]
    assert capture["complete"] is False
    assert capture["omitted"] > 0
    assert "cap" in str(capture["reason"])
    prompts = local_dir(leaf_item.store) / "prompts"
    for path in prompts.rglob("*"):
        if path.is_file():
            assert path.stat().st_size < 256 * 1024


def test_success_files_the_rail_report_block(leaf_item, fake_droid, droid_env) -> None:
    r = dispatch(leaf_item, "report-ok")
    assert r["ok"], r
    assert r["value"]["status"] == "verified"
    reports = records_of(leaf_item.store, "report.filed")
    assert len(reports) == 1
    data = reports[0]["data"]
    assert data["schema"] == 1
    assert data["target_class"] == "item"
    assert data["what_happened"] == "I wrote the failing test first and made it pass."
    assert data["nothing_to_report"] is False
    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["report"] == reports[0]["id"]


def test_missing_report_files_an_environment_report(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors(["malformed_json"])
    r = dispatch(leaf_item, "report-missing")
    assert r["ok"], r
    assert r["value"]["status"] != "verified"
    reports = records_of(leaf_item.store, "report.filed")
    assert len(reports) == 1
    data = reports[0]["data"]
    assert data["target_class"] == "environment"
    assert data["nothing_to_report"] is False
    assert data["observed"]
    run = latest_record(leaf_item.store, "run.completed")
    assert run["data"]["report"] == reports[0]["id"]


def test_secrets_are_scrubbed_and_redacted(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-unrelated-value")
    monkeypatch.setenv("GIT_DIR", "C:/elsewhere")
    fake_droid.set_behaviors(
        [{"mode": "is_error", "stderr": "SECRET-TEST-7f3a reached stderr"}]
    )
    r = dispatch(leaf_item, "secret")
    assert r["ok"], r

    env = fake_droid.log()[0]["env"]
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
    ):
        assert name in env, name
    assert env["RAIL_ACTOR"] == "implementer"
    assert "GITHUB_TOKEN" not in env
    assert not any(name.startswith("GIT_") for name in env)

    texts: list[str] = []
    for folder in (leaf_item.store, local_dir(leaf_item.store)):
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".md", ".txt", ".log"):
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
    for entry in fake_droid.log():
        texts.append(json.dumps(entry["argv"]))
        texts.append(entry["prompt"])
    combined = "\n".join(texts)
    assert "SECRET-TEST-7f3a" not in combined
    assert "[redacted]" in combined
