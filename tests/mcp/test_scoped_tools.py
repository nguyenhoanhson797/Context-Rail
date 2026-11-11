"""MCP lists only the tools the caller's seat may use."""

from __future__ import annotations

from types import SimpleNamespace

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import TOOLS
from helpers import as_human, mcp_call, mcp_list, open_test_session


def validator_session(role_store: SimpleNamespace) -> str:
    with as_human():
        added = invoke(
            "add",
            {
                "preset": "task",
                "title": "MCP review task",
                "rel": [f"part_of={role_store.o1}"],
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(added), added
        task = added["value"]["slug"]
        granted = invoke(
            "seat grant",
            {"seat": "validator-1", "role": "reviewer", "anchor": task},
            store=role_store.store,
            actor=None,
        )
        assert is_ok(granted), granted
    return open_test_session(role_store.store, "validator-1")


def test_a_validator_session_lists_only_read_and_report_tools(
    role_store: SimpleNamespace,
) -> None:
    session = validator_session(role_store)

    listed = {tool["name"] for tool in mcp_list(role_store.store, session=session)}

    assert "report" in listed
    assert "status" in listed
    assert "add" not in listed
    assert "merge" not in listed


def test_a_lead_session_lists_its_can_tools(role_store: SimpleNamespace) -> None:
    session = open_test_session(role_store.store, role_store.lead)

    listed = {tool["name"] for tool in mcp_list(role_store.store, session=session)}

    assert {"add", "dispatch", "review", "merge", "cto tick"} <= listed
    assert "init" not in listed


def test_legacy_stdio_lists_every_tool(leaf_item: SimpleNamespace) -> None:
    listed = {tool["name"] for tool in mcp_list(leaf_item.store, actor="cto")}

    assert listed == {tool.name for tool in TOOLS}


def test_calling_an_unlisted_tool_is_refused_by_the_guard(
    role_store: SimpleNamespace,
) -> None:
    session = validator_session(role_store)

    outcome = mcp_call(
        role_store.store,
        "add",
        {"preset": "task", "title": "MCP bypass"},
        session=session,
    )

    assert outcome["is_error"] is True
    error = outcome["structured"]["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "method_not_in_role"
