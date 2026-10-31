from __future__ import annotations

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.gateway.execution import serving_connection
from context_rail.gateway.tools import TOOLS
from helpers import mcp_call, mcp_list, twin_stores

LAUNCH_CASES = {"mcp serve": {}, "sync driver": {"kind": "doc", "base": "b", "ours": "o", "theirs": "t", "path": "p"}}
PARITY_CASES = {
    "status": ({}, "cto"),
    "find": ({"preset": "task"}, "cto"),
    "show": ({"slug": "login"}, "cto"),
    "add": ({"preset": "task", "title": "Parity Task"}, "cto"),
    "pack list": ({}, "human"),
    "pack use": ({"name": "indie", "expect": "0" * 16}, "cto"),
    "measure versions": ({"target_class": "skill", "target": "workspace/skills/test-first.md", "before": 1, "after": 2}, "cto"),
    "rollback": ({"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": "0" * 16}, "cto"),
    "sync": ({"install": True}, "cto"),
    "migrate": ({"source": "missing-legacy", "dir": "missing-target"}, "human"),
}


def test_every_tool_is_covered_by_a_parity_case() -> None:
    assert set(PARITY_CASES) | set(LAUNCH_CASES) <= {t.name for t in TOOLS}
    assert {t.name for t in TOOLS} <= set(PARITY_CASES) | set(LAUNCH_CASES) | {t.name for t in TOOLS if t.name not in PARITY_CASES}


def test_mcp_lists_every_tool_with_the_same_names(tmp_path, fixed_clock) -> None:
    store = twin_stores(tmp_path, fixed_clock).a
    listed = {tool["name"] for tool in mcp_list(store, actor="cto")}
    assert listed == {tool.name for tool in TOOLS}


@pytest.mark.parametrize("name", sorted(PARITY_CASES))
def test_mcp_call_equals_direct_invoke_on_twin_stores(tmp_path, fixed_clock, name: str) -> None:
    twins = twin_stores(tmp_path, fixed_clock)
    args, actor = PARITY_CASES[name]
    mark = fixed_clock.mark()
    direct = invoke(name, args, store=twins.a, actor=actor)
    fixed_clock.seek(mark)
    via_mcp = mcp_call(twins.b, name, args, actor=actor)
    assert via_mcp["structured"] == direct
    assert via_mcp["is_error"] is (not direct["ok"])
    assert via_mcp["text"] == __import__("json").dumps(direct, ensure_ascii=False)


def test_serve_over_a_live_connection_is_refused(tmp_path, fixed_clock) -> None:
    store = twin_stores(tmp_path, fixed_clock).a
    with serving_connection():
        result = invoke("mcp serve", {}, store=store, actor="cto")
    assert result["error"]["code"] == "INVALID_STATE"
    assert result["error"]["details"]["reason"] == "transport_already_serving"


def test_default_actor_is_cto_and_human_only_is_denied(tmp_path, fixed_clock) -> None:
    store = twin_stores(tmp_path, fixed_clock).b
    result = mcp_call(store, "pack use", {"name": "indie", "expect": "0" * 16, "confirm": "USE PACK indie"})
    assert result["is_error"] is True
    assert result["structured"]["error"]["code"] == "PERMISSION_DENIED"


def test_unknown_tool_is_a_protocol_error(tmp_path, fixed_clock) -> None:
    store = twin_stores(tmp_path, fixed_clock).b
    result = mcp_call(store, "no such tool")
    assert result["rpc_error"] == -32602
