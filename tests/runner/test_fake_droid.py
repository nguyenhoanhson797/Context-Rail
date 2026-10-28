"""The scripted fake droid, exercised through a real child process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from context_rail.model.cto import UNKNOWN, USAGE_KEYS, load_builtin_profile
from context_rail.runner.droid import (
    DROID_OUTPUT_CAP,
    droid_profile,
    extract_block,
    run_droid,
)
from context_rail.runner.run import run
from helpers import process_alive, run_git

IMPL = droid_profile(load_builtin_profile("implementer"))
REVIEW = droid_profile(load_builtin_profile("reviewer"))

SECRET = "SECRET-TEST-7f3a"


def _launch(
    tmp_path: Path,
    *,
    profile=IMPL,
    worktree: Path | None = None,
    timeout: float = 60,
    session_id: str | None = None,
    failure_text: str | None = None,
):
    root = Path(worktree or tmp_path)
    role = tmp_path / "role.md"
    order = tmp_path / "order.md"
    role.write_text("# Role\n\nImplement the item.\n", encoding="utf-8", newline="\n")
    order.write_text('{"schema": 1}\n\n## objective\n\nDo it.\n', encoding="utf-8", newline="\n")
    return run_droid(
        context=None,
        profile=profile,
        worktree=root,
        confine_to=root,
        role_file=role,
        order_file=order,
        timeout=timeout,
        session_id=session_id,
        failure_text=failure_text,
    )


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
        )
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def test_fake_droid_runs_through_a_real_child(fake_droid, droid_env, tmp_path):
    result = _launch(tmp_path, timeout=30)
    assert result.exit == 0 and result.session_id and result.ok
    assert fake_droid.log()[0]["argv"][1:3] == ["exec", "-o"]


def test_missing_usage_is_all_unknown(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["missing_usage"])
    result = _launch(tmp_path)
    assert result.ok and not result.malformed_usage
    assert result.usage == dict.fromkeys(USAGE_KEYS, UNKNOWN)


def test_reported_usage_keeps_zero(fake_droid, droid_env, tmp_path):
    result = _launch(tmp_path)
    assert result.usage["input_tokens"] == 120
    assert result.usage["factory_credits"] == 0.25


def test_malformed_usage_fails_the_run_and_salvages_the_valid_counters(
    fake_droid, droid_env, tmp_path
):
    fake_droid.set_behaviors(["malformed_usage"])
    result = _launch(tmp_path)
    assert result.malformed_usage is True and result.ok is False
    assert result.usage["output_tokens"] == 2
    assert result.usage["input_tokens"] == UNKNOWN
    assert "malformed usage" in (result.failure or "")


def test_malformed_json_is_a_failed_execution(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["malformed_json"])
    result = _launch(tmp_path)
    assert result.ok is False and result.result is None and result.failure


def test_truncated_json_is_a_failed_execution(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["truncated_json"])
    result = _launch(tmp_path)
    assert result.ok is False and result.result is None and result.failure


def test_nonzero_exit_fails_even_with_a_valid_result(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["nonzero_exit"])
    result = _launch(tmp_path)
    assert result.exit == 3 and result.ok is False
    assert result.result is None and "3" in (result.failure or "")


def test_is_error_is_a_failed_execution(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["is_error"])
    result = _launch(tmp_path)
    assert result.exit == 0 and result.ok is False and "is_error" in (result.failure or "")


def test_large_output_is_capped_and_never_a_success(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["large_output"])
    result = _launch(tmp_path)
    assert result.ok is False
    assert result.runner["omitted_bytes"] > 0
    assert len(result.runner["stdout"].encode("utf-8")) <= DROID_OUTPUT_CAP
    assert "cap" in (result.failure or "")


def test_a_hung_droid_is_killed_at_the_timeout(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["timeout"])
    started = time.monotonic()
    result = _launch(tmp_path, timeout=5)
    elapsed = time.monotonic() - started
    child_file = fake_droid.root / "fake-droid-child.json"
    pid = json.loads(child_file.read_text(encoding="utf-8"))["pid"]
    try:
        assert result.timed_out is True and result.ok is False
        assert elapsed < 60
        deadline = time.monotonic() + 15
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not process_alive(pid)
    finally:
        _kill(pid)


def test_a_failed_execution_still_reports_its_session_for_the_retry(
    fake_droid, droid_env, tmp_path
):
    fake_droid.set_behaviors(["nonzero_exit"])
    result = _launch(tmp_path)
    assert result.ok is False and result.failure
    assert result.session_id
    fake_droid.set_behaviors(["large_output"])
    capped = _launch(tmp_path)
    assert capped.session_id is None and capped.text == ""


def test_retry_continues_the_session_with_the_bounded_failure(
    fake_droid, droid_env, tmp_path
):
    fake_droid.set_behaviors(["fail_then_success"])
    first = _launch(tmp_path)
    assert first.ok is False and first.session_id
    second = _launch(
        tmp_path,
        session_id=first.session_id,
        failure_text="Check unit failed\nassert 1 == 2\nstderr excerpt",
    )
    assert second.ok is True
    log = fake_droid.log()
    assert len(log) == 2
    retry_argv = log[1]["argv"]
    assert retry_argv[1:7] == ["exec", "-o", "json", "-s", first.session_id, "--cwd"]
    assert retry_argv[retry_argv.index("--auto") + 1] == "medium"
    assert "-r" not in retry_argv
    assert "Check unit failed" in log[1]["prompt"]
    assert "stderr excerpt" in log[1]["prompt"]
    assert fake_droid.session_ids()[1] == first.session_id


def test_review_verdicts_are_extracted(fake_droid, droid_env, tmp_path):
    fake_droid.set_behaviors(["review_approve"])
    approved = _launch(tmp_path, profile=REVIEW)
    review = extract_block(approved.text, "rail-review")
    assert review is not None and review["verdict"] == "approve"
    assert extract_block(approved.text, "rail-report") is not None
    fake_droid.set_behaviors(["review_reject"])
    rejected = _launch(tmp_path, profile=REVIEW)
    review = extract_block(rejected.text, "rail-review")
    assert review is not None and review["verdict"] == "reject"


def test_the_report_block_is_extracted(fake_droid, droid_env, tmp_path):
    result = _launch(tmp_path)
    report = extract_block(result.text, "rail-report")
    assert report is not None
    assert report["schema"] == 1 and report["target_class"] == "item"


def test_secrets_never_reach_argv_prompts_or_the_log(fake_droid, droid_env, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "C:/somewhere/else")
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated-token")
    fake_droid.set_behaviors([{"mode": "nonzero_exit", "stderr": f"auth failed {SECRET}"}])
    result = _launch(tmp_path)
    assert SECRET not in result.runner["stdout"]
    assert SECRET not in result.runner["stderr"]
    assert "[redacted]" in result.runner["stderr"]
    record = fake_droid.log()[0]
    assert SECRET not in json.dumps(record["argv"])
    assert SECRET not in record["prompt"]
    assert record["env"]["RAIL_ACTOR"] == "implementer"
    assert record["env"]["FACTORY_API_KEY"] == "[redacted]"
    assert "GIT_DIR" not in record["env"] and "GITHUB_TOKEN" not in record["env"]
    assert "PATH" in record["env"]
    log_text = (fake_droid.root / "fake-droid-argv.jsonl").read_text(encoding="utf-8")
    assert SECRET not in log_text
    for name in ("role.md", "order.md"):
        assert SECRET not in (tmp_path / name).read_text(encoding="utf-8")


def test_list_tools_is_supported(fake_droid, droid_env, tmp_path):
    result = run(
        [sys.executable, str(fake_droid.script), "--list-tools"],
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=60,
    )
    assert result["exit"] == 0
    assert "read-cli" in result["stdout"] and "execute-cli" in result["stdout"]


def test_an_unexpected_flag_is_refused(fake_droid, droid_env, tmp_path):
    result = run(
        [sys.executable, str(fake_droid.script), "exec", "--enabled-tools", "read-cli"],
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=60,
    )
    assert result["exit"] != 0
    assert "--enabled-tools" in result["stderr"]


def test_success_mode_leaves_one_deterministic_commit(fake_droid, droid_env, tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    run_git(worktree, "init")
    (worktree / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    run_git(worktree, "add", "-A")
    run_git(worktree, "commit", "-m", "base")
    base = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    result = _launch(tmp_path, worktree=worktree)
    assert result.ok is True
    head = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert head != base
    assert (worktree / "fake-droid-work.txt").is_file()
    subject = run_git(worktree, "log", "--format=%s", "-1").stdout.strip()
    assert subject == "fake droid candidate"
