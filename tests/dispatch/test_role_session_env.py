"""Role-mode runs hand the child a session; legacy runs keep ``RAIL_ACTOR``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.gateway.handlers.dispatch import implementer as implementer_handler
from context_rail.model.cto import load_builtin_profile
from context_rail.runner import protocol
from context_rail.runner.droid import droid_env
from context_rail.runner.protocol import run_runner
from context_rail.store.sessions import resolve_session
from context_rail.store.location import local_dir
from helpers import FakeDroid, open_test_session


def dispatch_as(
    store: Path, seat: str, item: str, etag: str, key: str
) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(
            "dispatch",
            {"item": item, "expect": etag, "profile": "worker", "key": key},
            store=store,
        )


def test_role_dispatch_gives_the_child_a_session_not_an_actor(
    role_git_store: SimpleNamespace, fake_droid: Any
) -> None:
    dispatched = dispatch_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        role_git_store.etag,
        "role-env-session",
    )

    assert is_ok(dispatched), dispatched
    env = fake_droid.log()[0]["env"]
    assert "RAIL_ACTOR" not in env
    assert env["RAIL_SESSION"]
    assert env["RAIL_RUN"] == dispatched["value"]["dispatch_id"]


def test_the_child_session_resolves_to_a_run_seat_scoped_to_the_item(
    role_git_store: SimpleNamespace, fake_droid: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(implementer_handler, "close_sessions", lambda *a, **k: 0)
    dispatched = dispatch_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        role_git_store.etag,
        "role-env-resolve",
    )
    assert is_ok(dispatched), dispatched

    token = fake_droid.log()[0]["env"]["RAIL_SESSION"]
    session = resolve_session(role_git_store.store, token)

    assert session is not None
    assert session.seat == f"implementer@{dispatched['value']['dispatch_id']}"
    assert session.role == "implementer"
    assert session.anchor == role_git_store.task
    assert session.profile == "worker"
    assert session.granted_by == role_git_store.lead


def test_a_run_seat_cannot_write_outside_its_item(
    role_git_store: SimpleNamespace,
    monkeypatch: Any,
) -> None:
    probe = FakeDroid(role_git_store.root.parent / "rail-probe", [])
    monkeypatch.setenv("RAIL_DROID_BIN", str(probe.script))
    report_file = role_git_store.root.parent / "sibling-report.json"
    probe.set_behaviors(
        [
            {
                "mode": "success",
                "rail": [
                    "--store",
                    str(role_git_store.store),
                    "report",
                    role_git_store.sibling,
                    "--run",
                    "01J00000000000000000000000",
                    "--report-file",
                    str(report_file),
                ],
            }
        ]
    )

    dispatched = dispatch_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        role_git_store.etag,
        "role-env-reach",
    )

    assert is_ok(dispatched), dispatched
    called = json.loads(
        (probe.root / "fake-droid-rail.json").read_text(encoding="utf-8")
    )
    assert called["exit"] == 1, called
    error = json.loads(called["stdout"])["error"]
    assert (error["code"], error["details"]["reason"]) == (
        "PERMISSION_DENIED",
        "write_out_of_reach",
    )


def test_the_run_session_is_closed_after_the_run_even_on_failure(
    role_git_store: SimpleNamespace, monkeypatch: Any
) -> None:
    probe = FakeDroid(role_git_store.root.parent / "failure-probe", ["nonzero_exit"])
    monkeypatch.setenv("RAIL_DROID_BIN", str(probe.script))
    dispatched = dispatch_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        role_git_store.etag,
        "role-env-close",
    )

    assert is_ok(dispatched), dispatched
    assert dispatched["value"]["status"] == "escalated"
    token = probe.log()[0]["env"]["RAIL_SESSION"]
    assert resolve_session(role_git_store.store, token) is None


def test_the_run_session_is_closed_when_the_prompts_cannot_be_written(
    role_git_store: SimpleNamespace, monkeypatch: Any
) -> None:
    sessions = local_dir(role_git_store.store) / "sessions"

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("prompt write failed")

    monkeypatch.setattr(implementer_handler, "_write_prompts", boom)
    dispatched = dispatch_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        role_git_store.etag,
        "role-env-prompt-failure",
    )

    assert not is_ok(dispatched)
    import json

    leftover = [
        json.loads(path.read_text(encoding="utf-8")).get("run")
        for path in sessions.glob("*.json")
    ]
    assert not any(leftover)


def test_the_non_droid_runner_gets_the_same_environment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    role_file = tmp_path / "role.md"
    order_file = tmp_path / "order.md"
    role_file.write_text("# Role\n", encoding="utf-8")
    order_file.write_text("## objective\n", encoding="utf-8")
    seen: dict[str, Any] = {}

    def capture(argv: list[str], **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "exit": 0,
            "timed_out": False,
            "duration_s": 0.0,
            "stdout": '{"type": "result", "result": "ok"}',
            "stderr": "",
            "omitted_bytes": 0,
            "digest": "sha256:test",
            "argv_digest": "sha256:test",
        }

    monkeypatch.setattr(protocol, "_run", capture)
    launched = run_runner(
        profile=load_builtin_profile("implementer"),
        worktree=tmp_path,
        confine_to=tmp_path,
        role_file=role_file,
        order_file=order_file,
        timeout=60,
        runner="local",
        session="session-token",
        run="run-1",
    )

    assert launched.ok is True
    assert seen["extra_env"]["RAIL_SESSION"] == "session-token"
    assert seen["extra_env"]["RAIL_RUN"] == "run-1"
    assert "RAIL_ACTOR" not in seen["extra_env"]


def test_legacy_dispatch_still_sets_rail_actor() -> None:
    assert droid_env("implementer", "droid")["RAIL_ACTOR"] == "implementer"
    extras = droid_env("implementer", "droid", session="token", run="run-1")
    assert "RAIL_ACTOR" not in extras
    assert extras["RAIL_SESSION"] == "token"
    assert extras["RAIL_RUN"] == "run-1"
