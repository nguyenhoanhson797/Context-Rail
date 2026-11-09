"""A refused verb names the read command that shows the fresh state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.gateway.gateway import invoke
from helpers import init_store


def _store(tmp_path: Path) -> Path:
    store = init_store(tmp_path)
    assert invoke(
        "add", {"preset": "task", "title": "A task"}, store=store, actor="cto"
    )["ok"]
    return store


def _command(result: dict[str, Any]) -> str:
    assert result["ok"] is False, result
    return result["error"]["details"]["command"]


def test_a_refused_move_points_at_show(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke(
        "move",
        {"slug": "ghost", "status": "done", "expect": "0123456789abcdef"},
        store=store,
        actor="cto",
    )

    assert _command(refused) == "rail read show ghost"


def test_a_refused_dispatch_points_at_the_approval(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke(
        "dispatch",
        {"item": "a-task", "expect": "0123456789abcdef", "key": "k"},
        store=store,
        actor="cto",
    )

    assert _command(refused).startswith("rail approve charter")


def test_a_refused_merge_points_at_the_approval(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke(
        "merge",
        {
            "item": "a-task",
            "run": "01JRUN000000000000000001",
            "expect": "0123456789abcdef",
            "key": "k",
        },
        store=store,
        actor="cto",
    )

    assert _command(refused).startswith("rail approve charter")


def test_dispatch_and_merge_fall_back_to_show_once_approved() -> None:
    from context_rail.gateway.gateway import _with_command

    refused = {"ok": False, "error": {"code": "NOT_FOUND", "details": {}}}

    dispatch = _with_command(dict(refused), "dispatch", {"item": "a-task"})
    merge = _with_command(dict(refused), "merge", {"item": "a-task"})

    assert dispatch["error"]["details"]["command"] == "rail read show a-task"
    assert merge["error"]["details"]["command"] == "rail read show a-task"


def test_a_refused_action_points_at_explain(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke(
        "do", {"action": "ghost", "item": "a-task"}, store=store, actor="cto"
    )

    assert _command(refused) == "rail explain ghost"


def test_an_existing_command_is_never_overwritten() -> None:
    from context_rail.gateway.gateway import _with_command

    result = {
        "ok": False,
        "error": {
            "code": "BUSINESS_RULE_VIOLATED",
            "details": {"command": "rail verify a-task"},
        },
    }

    assert _with_command(result, "move", {"slug": "a-task"})["error"]["details"][
        "command"
    ] == "rail verify a-task"


def test_an_unrelated_tool_gets_no_invented_command(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke("show", {"slug": "ghost"}, store=store, actor="cto")

    assert refused["ok"] is False
    assert "command" not in (refused["error"].get("details") or {})
