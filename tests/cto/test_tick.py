"""The single CTO tick: lockout, launch, records, timeout and accounting."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.cto import USAGE_KEYS, load_builtin_profile
from context_rail.store.location import local_dir
from helpers import latest_record, records_of, run_cli, write_run

MODEL = "claude-sonnet-4-5"
TOOLS_CSV = "read-cli,ls-cli,grep_tool_cli,glob-search-cli,create-cli,edit-cli,execute-cli"
RESULT_KEYS = {"run", "session_id", "status", "reports", "usage"}


def tick(store: Path, key: str, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"key": key}
    args.update(extra)
    return invoke("cto tick", args, store=store, actor="cto")


def tick_codes(store: Path) -> list[str]:
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    return [issue["code"] for issue in result["value"]["issues"]]


def _record_count(store: Path, kind: str) -> int:
    return len(records_of(store, kind))


# ── approval lockout ──────────────────────────────────────────────────────


def test_tick_refused_before_approval(unapproved_store, fake_droid) -> None:
    r = tick(unapproved_store, "t0")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_missing"
    assert fake_droid.log() == []
    assert _record_count(unapproved_store, "tick.started") == 0
    assert _record_count(unapproved_store, "run.completed") == 0

    brief = invoke("cto brief", {}, store=unapproved_store, actor="cto")
    assert is_ok(brief), brief


def test_a_mismatched_picture_refuses_the_tick(approved_store, fake_droid) -> None:
    charter = approved_store / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nOne more line.\n",
        encoding="utf-8",
        newline="\n",
    )

    r = tick(approved_store, "t0m")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_mismatch"
    assert fake_droid.log() == []


def test_cli_tick_exits_one_before_approval(unapproved_store) -> None:
    completed = run_cli(
        unapproved_store.parent, "cto", "tick", "--key", "t0c", store=unapproved_store
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1, completed.stderr
    assert payload["error"]["code"] == "INVALID_STATE"
    assert payload["error"]["details"]["reason"] == "approval_missing"


# ── launch ────────────────────────────────────────────────────────────────


def test_tick_launches_with_actor_and_high_autonomy(
    leaf_item, fake_droid, droid_env
) -> None:
    r = tick(leaf_item.store, "t1")

    assert is_ok(r), r
    assert set(r["value"]) == RESULT_KEYS
    assert r["value"]["status"] == "completed"
    launch = fake_droid.log()[0]
    run_record = latest_record(leaf_item.store, "run.completed")
    tick_id = run_record["data"]["dispatch_id"]
    prompts = local_dir(leaf_item.store) / "prompts" / f"tick-{tick_id}"
    assert launch["argv"][0] == str(fake_droid.script)
    assert launch["argv"][1:] == [
        "exec",
        "-o",
        "json",
        "--cwd",
        str(leaf_item.root.resolve()),
        "-m",
        MODEL,
        "--restrict-tools",
        TOOLS_CSV,
        "--append-system-prompt-file",
        str((prompts / "role.md").absolute()),
        "-f",
        str((prompts / "order.md").absolute()),
        "--auto",
        "high",
    ]
    assert "-r" not in launch["argv"]
    assert launch["env"]["RAIL_ACTOR"] == "cto"
    assert launch["env"]["FACTORY_API_KEY"] == "[redacted]"

    order = (prompts / "order.md").read_text(encoding="utf-8")
    assert f"--store {leaf_item.store.absolute()}" in order
    assert "Ship invoicing" in order
    assert "rail-report" in order
    assert str(leaf_item.root.resolve()) in order
    role = (prompts / "role.md").read_text(encoding="utf-8")
    assert "acceptance" in role


def test_tick_never_runs_in_a_dispatch_worktree(leaf_item, fake_droid, droid_env) -> None:
    assert is_ok(tick(leaf_item.store, "t1b"))

    argv = fake_droid.log()[0]["argv"]
    cwd = Path(argv[argv.index("--cwd") + 1])
    assert cwd == leaf_item.root.resolve()
    assert "context-rail-worktrees" not in cwd.as_posix()


def test_cli_tick_prints_the_pinned_result(leaf_item, fake_droid, droid_env) -> None:
    completed = run_cli(
        leaf_item.root, "cto", "tick", "--key", "t1c", "--timeout", "300", store=leaf_item.store
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert set(payload["value"]) == RESULT_KEYS
    assert payload["value"]["status"] == "completed"


# ── records, reports and accounting ───────────────────────────────────────


def test_tick_records_its_run_and_one_report(leaf_item, fake_droid, droid_env) -> None:
    r = tick(leaf_item.store, "t2")
    assert is_ok(r), r

    started = latest_record(leaf_item.store, "tick.started")
    assert started["data"]["schema"] == 1
    assert started["data"]["dispatch_id"] == latest_record(
        leaf_item.store, "run.completed"
    )["data"]["dispatch_id"]

    run_record = latest_record(leaf_item.store, "run.completed")
    data = run_record["data"]
    assert data["role"] == "cto"
    assert data["profile"] == "cto"
    assert data["actor"] == "cto"
    assert data["model"] == MODEL
    assert data["item"] is None
    assert data["candidate_commit"] is None
    assert data["session_id"] == r["value"]["session_id"]
    assert data["session_id"]
    assert data["started_at"] and data["ended_at"]
    assert data["duration_s"] >= 0
    assert data["status"] == "completed"
    assert data["capture"] == {"complete": True, "reason": None, "omitted": 0}
    assert data["usage"]["known_sum"]["input_tokens"] == 120
    assert data["usage"]["known_sum"]["factory_credits"] == 0.25

    assert len(records_of(leaf_item.store, "report.filed")) == 1
    report = latest_record(leaf_item.store, "report.filed")
    assert data["report"] == report["id"]
    assert r["value"]["reports"] == [report["id"]]
    assert r["value"]["run"] == run_record["id"]
    assert set(USAGE_KEYS) <= set(r["value"]["usage"])
    assert r["value"]["usage"]["input_tokens"] == 120


def test_tick_record_and_worker_records_both_show_in_ledger(
    leaf_item, fake_droid, droid_env
) -> None:
    write_run(
        leaf_item.store,
        "01JWORKERRUN00000000000042",
        item=leaf_item.slug,
        dispatch="dispatch-worker",
        usage={"input_tokens": 7, "output_tokens": 5, "factory_credits": 1},
    )

    r = tick(leaf_item.store, "t3")
    assert is_ok(r), r
    ledger = invoke("ledger", {}, store=leaf_item.store, actor="cto")
    assert is_ok(ledger), ledger
    value = ledger["value"]

    roles = {row["role"] for row in value["runs"]}
    assert {"cto", "implementer"} <= roles
    assert value["total"] >= 2
    tick_rows = [row for row in value["runs"] if row["role"] == "cto"]
    assert len(tick_rows) == 1
    assert tick_rows[0]["item"] is None
    assert tick_rows[0]["run"] == r["value"]["run"]
    assert value["totals"]["known_sum"]["input_tokens"] == 120 + 7
    assert value["totals"]["known_sum"]["output_tokens"] == 40 + 5


def test_a_completed_tick_leaves_no_missing_report(leaf_item, fake_droid, droid_env) -> None:
    assert is_ok(tick(leaf_item.store, "t4"))

    codes = tick_codes(leaf_item.store)
    assert "report_missing" not in codes
    assert "run_incomplete" not in codes


def test_an_interrupted_tick_is_reported_as_an_incomplete_run(leaf_item) -> None:
    assert "run_incomplete" not in tick_codes(leaf_item.store)

    from helpers import write_record

    write_record(
        leaf_item.store,
        "01JORPHANTICK0000000000001",
        "tick.started",
        {
            "schema": 1,
            "dispatch_id": "01JORPHANTICK0000000000001",
            "timeout_s": 7200,
            "actor": "cto",
            "profile": "cto",
            "profile_version": 1,
            "profile_etag": "0" * 16,
            "store": str(leaf_item.store.absolute()),
            "checkout": str(leaf_item.root.absolute()),
            "model": MODEL,
            "autonomy": "high",
        },
    )

    assert "run_incomplete" in tick_codes(leaf_item.store)


# ── timeout ───────────────────────────────────────────────────────────────


@pytest.mark.timeout(120)
def test_tick_timeout_fails_the_run_and_releases_the_lock(
    leaf_item, fake_droid, droid_env
) -> None:
    fake_droid.set_behaviors(["timeout"])
    failed = tick(leaf_item.store, "t5", timeout=5)

    assert is_ok(failed), failed
    assert failed["value"]["status"] == "failed"
    run_record = latest_record(leaf_item.store, "run.completed")
    assert run_record["data"]["status"] == "failed"
    assert run_record["data"]["attempts"][0]["timed_out"] is True
    assert run_record["data"]["report"] is not None
    assert len(records_of(leaf_item.store, "report.filed")) >= 1

    fake_droid.set_behaviors(["success"])
    assert is_ok(tick(leaf_item.store, "t6"))


def test_tick_refuses_a_non_positive_timeout(leaf_item, fake_droid) -> None:
    r = tick(leaf_item.store, "t7", timeout=0)

    assert r["ok"] is False, r
    assert r["error"]["code"] == "VALIDATION_FAILED"
    assert fake_droid.log() == []


def test_tick_refuses_a_timeout_above_the_profile_cap(leaf_item, fake_droid) -> None:
    r = tick(leaf_item.store, "t7b", timeout=7201)

    assert r["ok"] is False, r
    assert r["error"]["code"] == "VALIDATION_FAILED"
    assert r["error"]["details"]["max_seconds"] == 7200
    assert fake_droid.log() == []


@pytest.mark.timeout(120)
def test_a_timed_out_tick_reports_unknown_counters_not_zero(
    leaf_item, fake_droid, droid_env
) -> None:
    fake_droid.set_behaviors(["timeout"])
    r = tick(leaf_item.store, "t7c", timeout=5)

    assert is_ok(r), r
    assert set(r["value"]["usage"]) == set(USAGE_KEYS)
    assert set(r["value"]["usage"].values()) == {"unknown"}
    run = latest_record(leaf_item.store, "run.completed")["data"]
    assert all(
        run["usage"]["unknown_attempts"][name] == 1 for name in USAGE_KEYS
    )


# ── nested refusal ────────────────────────────────────────────────────────


def test_nested_tick_is_refused_for_a_worker_actor(
    leaf_item, fake_droid, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "implementer")

    r = invoke("cto tick", {"key": "t8"}, store=leaf_item.store)

    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert fake_droid.log() == []


def test_nested_tick_is_refused_inside_a_cto_session(
    leaf_item, fake_droid, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "cto")

    r = invoke("cto tick", {"key": "t8b"}, store=leaf_item.store)

    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert fake_droid.log() == []


def test_nested_tick_is_not_allowed_by_the_cto_profile(leaf_item) -> None:
    profile = load_builtin_profile("cto")

    assert "cto brief" in profile.allowed_methods
    assert "cto tick" not in profile.allowed_methods
    stored = leaf_item.store / "workspace" / "cto" / "profile.md"
    text = stored.read_text(encoding="utf-8")
    assert "cto brief" in text
    allowed = text.split("allowed_methods:", 1)[1].split("budget:", 1)[0]
    assert "cto tick" not in allowed


def test_an_explicit_worker_actor_cannot_tick(leaf_item, fake_droid) -> None:
    r = invoke("cto tick", {"key": "t8c"}, store=leaf_item.store, actor="implementer")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "PERMISSION_DENIED"
    assert fake_droid.log() == []


# ── idempotency ───────────────────────────────────────────────────────────


def test_a_completed_tick_replays_for_the_same_key(leaf_item, fake_droid, droid_env) -> None:
    first = tick(leaf_item.store, "t9")
    assert is_ok(first), first
    second = tick(leaf_item.store, "t9")

    assert is_ok(second), second
    assert second["value"] == first["value"]
    assert len(fake_droid.log()) == 1
    assert _record_count(leaf_item.store, "run.completed") == 1
    assert _record_count(leaf_item.store, "tick.started") == 1
    assert _record_count(leaf_item.store, "report.filed") == 1


def test_a_pending_tick_key_conflicts(leaf_item, fake_droid) -> None:
    from context_rail.runner import dispatch as local_dispatch

    local_dispatch.write_tick_binding(
        leaf_item.store, "t10", tick_id="01JPENDINGTICK00000000001", state="started"
    )

    r = tick(leaf_item.store, "t10")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "CONFLICT"
    assert r["error"]["details"]["tick_id"] == "01JPENDINGTICK00000000001"
    assert fake_droid.log() == []


def test_a_tick_needs_a_key(leaf_item, fake_droid) -> None:
    r = invoke("cto tick", {}, store=leaf_item.store, actor="cto")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "VALIDATION_FAILED"
    assert fake_droid.log() == []


def test_a_tick_needs_a_canonical_checkout(approved_store, fake_droid) -> None:
    r = tick(approved_store, "t11")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INTEGRATION_FAILURE"
    assert fake_droid.log() == []


def test_the_tick_run_shape_is_pinned(leaf_item, fake_droid, droid_env) -> None:
    from context_rail.model.cto import parse_run

    assert is_ok(tick(leaf_item.store, "t12"))

    data = latest_record(leaf_item.store, "run.completed")["data"]
    parse_run(data)
    assert data["status"] in ("completed", "failed")
    assert data["attempts"][0]["session_id"] == data["session_id"]
    assert data["runner_digest"]


def test_the_work_order_binds_the_profile_etag(leaf_item, fake_droid, droid_env) -> None:
    from context_rail.runner.droid import _work_order_metadata

    started = tick(leaf_item.store, "t-etag")
    assert is_ok(started), started
    run_record = latest_record(leaf_item.store, "run.completed")
    tick_id = run_record["data"]["dispatch_id"]
    order = local_dir(leaf_item.store) / "prompts" / f"tick-{tick_id}" / "order.md"
    metadata, _end = json.JSONDecoder().raw_decode(order.read_text(encoding="utf-8"))
    shown = invoke("show", {"slug": "cto"}, store=leaf_item.store, actor="cto")
    assert is_ok(shown), shown
    profile_etag = shown["value"]["doc"]["etag"]
    approval = latest_record(leaf_item.store, "approval.recorded")

    assert metadata["profile_etag"] == profile_etag
    assert metadata["profile_etag"] != approval["id"]
    assert run_record["data"]["profile_etag"] == metadata["profile_etag"]
    assert (
        _work_order_metadata(metadata, metadata["dispatch_id"])["profile_etag"]
        == profile_etag
    )
