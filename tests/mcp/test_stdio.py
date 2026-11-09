from __future__ import annotations

import json

import anyio

from helpers import init_store, stdio_session


def test_the_mcp_server_reports_the_package_version() -> None:
    from context_rail import __version__
    from context_rail.gateway.mcp_server import build_server

    options = build_server(store=None, actor="cto").create_initialization_options()

    assert options.server_name == "context-rail"
    assert options.server_version == __version__


def test_stdio_lists_reads_replays_and_shuts_down_cleanly(tmp_path) -> None:
    store = init_store(tmp_path)

    async def scenario() -> None:
        async with stdio_session(store, actor="cto") as session:
            tools = await session.list_tools()
            assert any(tool.name == "status" for tool in tools.tools)
            first = await session.call_tool("add", {"preset": "task", "title": "Ünïcode 漢字 🙂", "key": "K-1"})
            second = await session.call_tool("add", {"preset": "task", "title": "Ünïcode 漢字 🙂", "key": "K-1"})
            assert first.structured_content == second.structured_content
            assert json.loads(first.content[0].text) == first.structured_content
            assert first.is_error is False

    anyio.run(scenario)


def test_stdio_refusal_is_an_is_error_result_not_a_protocol_error(tmp_path) -> None:
    store = init_store(tmp_path)

    async def scenario() -> None:
        async with stdio_session(store) as session:
            result = await session.call_tool("pack use", {"name": "indie", "expect": "0" * 16})
            assert result.is_error is True
            assert result.structured_content["error"]["code"] == "PERMISSION_DENIED"

    anyio.run(scenario)
