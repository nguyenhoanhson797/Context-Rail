"""A served command skips the gateway and still answers as a direct call would."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from helpers import run_cli_probe, serving_daemon, without_perf
from perf.gen import make_store

_STALE_ETAG = "0123456789abcdef"


@pytest.fixture
def store(tmp_path: Path, daemon_enabled: None) -> Path:
    return make_store(tmp_path, 5)


def _titles(store: Path) -> list[str]:
    found = invoke("find", {"preset": "task", "limit": 100}, store=store, actor="human")
    return [item["title"] for item in found["value"]["items"]]


def test_a_served_call_never_imports_the_gateway(store: Path) -> None:
    with serving_daemon(store):
        proc, loaded = run_cli_probe(store.parent, "next", store=store)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    assert loaded == {"GW": False, "YAML": False}


def test_discovery_without_store_from_a_deep_directory(store: Path) -> None:
    deep = store / "notes" / "deeper"
    deep.mkdir(parents=True)
    with serving_daemon(store):
        proc, loaded = run_cli_probe(deep, "next")
    assert proc.returncode == 0, proc.stderr
    assert loaded == {"GW": False, "YAML": False}


def test_a_relative_store_is_resolved_against_the_callers_directory(store: Path) -> None:
    with serving_daemon(store):
        proc, loaded = run_cli_probe(store.parent, "next", store=Path(store.name))
    assert proc.returncode == 0, proc.stderr
    assert loaded == {"GW": False, "YAML": False}


def test_a_path_argument_runs_direct_and_reads_the_callers_file(
    store: Path, tmp_path: Path
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "body.md").write_text("Body from the caller\n", encoding="utf-8", newline="\n")
    with serving_daemon(store):
        proc, loaded = run_cli_probe(
            caller,
            "--actor",
            "human",
            "change",
            "add",
            "task",
            "From a file",
            "--body-file",
            "body.md",
            store=store,
        )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    assert loaded["GW"] is True
    shown = invoke("show", {"slug": "from-a-file"}, store=store, actor="human")
    assert shown["value"]["body"] == "Body from the caller\n"


@pytest.mark.parametrize("opt_out", ["flag", "environment"])
def test_opt_outs_run_direct(store: Path, opt_out: str) -> None:
    args = ["--no-daemon", "next"] if opt_out == "flag" else ["next"]
    env = {"RAIL_NO_DAEMON": "1"} if opt_out == "environment" else None
    with serving_daemon(store):
        proc, loaded = run_cli_probe(store.parent, *args, store=store, env=env)
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is True


def test_an_ineligible_tool_runs_direct(store: Path) -> None:
    with serving_daemon(store):
        proc, loaded = run_cli_probe(store.parent, "doctor", store=store)
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is True


@pytest.mark.parametrize(
    ("args", "code"), [(("show", "missing"), 1), (("status",), 0)], ids=["refusal", "read"]
)
def test_exit_codes_match_a_direct_call(store: Path, args: tuple[str, ...], code: int) -> None:
    with serving_daemon(store):
        served, served_loaded = run_cli_probe(store.parent, *args, store=store)
        direct, direct_loaded = run_cli_probe(
            store.parent, "--no-daemon", *args, store=store
        )
    assert served_loaded["GW"] is False and direct_loaded["GW"] is True
    assert served.returncode == direct.returncode == code
    assert without_perf(json.loads(served.stdout)) == without_perf(json.loads(direct.stdout))


def test_the_callers_actor_env_reaches_the_daemon(
    approved_store: Path, daemon_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = approved_store
    added = invoke("add", {"preset": "task", "title": "Leaf work"}, store=store, actor="cto")
    assert added["ok"] is True, added
    monkeypatch.setenv("RAIL_ACTOR", "human")
    move = ("change", "move", "leaf-work", "doing", "--expect", _STALE_ETAG)
    with serving_daemon(store):
        proc, loaded = run_cli_probe(
            store.parent, *move, store=store, env={"RAIL_ACTOR": "implementer"}
        )
    assert loaded["GW"] is False
    assert proc.returncode == 1, proc.stderr
    error: dict[str, Any] = json.loads(proc.stdout)["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["profile"] == "implementer"


def test_a_served_write_creates_exactly_one_document(store: Path) -> None:
    with serving_daemon(store):
        proc, loaded = run_cli_probe(
            store.parent, "--actor", "human", "change", "add", "task", "Served", store=store
        )
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is False
    assert _titles(store).count("Served") == 1
