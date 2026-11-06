"""Default-deny: which tools and calls a daemon may serve."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from context_rail.gateway.tools import SURFACE_TOOLS, TOOLS, daemon_eligible
from helpers import assert_parity, serving_daemon

_FLAGGED = frozenset(
    {
        "status",
        "find",
        "show",
        "types",
        "next",
        "model",
        "explain",
        "why",
        "cto brief",
        "ci",
        "digest",
        "ledger",
        "add",
        "update",
        "move",
        "link",
    }
)
_PATH_NAMES = frozenset(
    {
        "dir",
        "root",
        "source",
        "answers_file",
        "draft_file",
        "body_file",
        "spec_file",
        "report_file",
        "worktree",
        "from",
        "path",
    }
)
_STAY_DIRECT = (
    "init",
    "migrate",
    "approve",
    "check",
    "verify",
    "dispatch",
    "review",
    "merge",
    "report",
    "triage",
    "do",
    "cto tick",
    "adapters sync",
    "pack list",
    "pack show",
    "pack validate",
    "pack create",
    "pack use",
    "rollback",
    "sync",
    "sync driver",
    "mcp serve",
    "ui serve",
    "serve daemon",
    "doctor",
    "reindex",
    "measure versions",
    "measure fix",
)
_BY_NAME = {tool.name: tool for tool in TOOLS}
_STALE = "0123456789abcdef"
_PARITY_ARGS: dict[str, dict[str, Any]] = {
    "find": {"preset": "task"},
    "show": {"slug": "t0"},
    "explain": {"name": "task"},
    "why": {"slug": "t0"},
    "status": {},
    "types": {},
    "next": {},
    "model": {},
    "cto brief": {},
    "ci": {},
    "digest": {},
    "ledger": {},
    "add": {"preset": "nosuch", "title": "X"},
    "update": {"slug": "t0", "expect": _STALE, "set": ["estimate=1"]},
    "move": {"slug": "t0", "status": "doing", "expect": _STALE},
    "link": {
        "src": "t0",
        "rel": "part_of",
        "dst": "g0",
        "expect": _STALE,
        "reason": "planning",
    },
}


def test_the_flagged_set_is_exactly_the_audited_one() -> None:
    assert {tool.name for tool in TOOLS if tool.daemon} == _FLAGGED


def test_no_flagged_tool_has_human_permission() -> None:
    assert [t.name for t in TOOLS if t.daemon and t.permission == "human"] == []


def test_no_flagged_tool_requires_a_path_parameter() -> None:
    needing = [
        tool.name
        for tool in TOOLS
        if tool.daemon and any(p.path and p.required for p in tool.params)
    ]
    assert needing == []


def test_path_parameters_are_marked() -> None:
    unmarked = [
        f"{tool.name}.{param.name}"
        for tool in TOOLS
        for param in tool.params
        if param.name in _PATH_NAMES and not param.path
    ]
    assert unmarked == []


def test_a_path_argument_makes_an_eligible_tool_ineligible() -> None:
    add = _BY_NAME["add"]
    assert daemon_eligible(add, {"body_file": "x.md"}) is False
    assert daemon_eligible(add, {}) is True
    assert daemon_eligible(add, {"body_file": None}) is True


def test_an_unflagged_tool_is_never_eligible() -> None:
    assert daemon_eligible(_BY_NAME["doctor"], {}) is False


@pytest.mark.parametrize("name", _STAY_DIRECT)
def test_these_tools_stay_direct(name: str) -> None:
    assert name in _BY_NAME, f"{name!r} is not a tool: fix this list"
    assert _BY_NAME[name].daemon is False


def test_alias_clones_keep_the_flag() -> None:
    surface = {tool.name: tool for tool in SURFACE_TOOLS}
    for name in ("overview", "gate", "read status", "change add", "types", "next"):
        assert surface[name].daemon is True, name
    for name in ("check run", "ops doctor", "init", "approve", "serve daemon"):
        assert surface[name].daemon is False, name


@pytest.mark.parametrize("tool", sorted(_FLAGGED))
def test_every_flagged_tool_matches_direct_from_another_directory(
    tool: str,
    store_factory: Callable[..., Path],
    daemon_enabled: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_factory(20)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("GITHUB_TOKEN", "decoy-token")
    monkeypatch.setenv("RAIL_RUNNER", "decoy-runner")
    monkeypatch.setenv("RAIL_ACTOR", "decoy-actor")
    with serving_daemon(store):
        assert_parity(store, tool, dict(_PARITY_ARGS[tool]))
