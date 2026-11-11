"""An MCP server is pinned to a session, never to an actor name."""

from __future__ import annotations

import anyio
from mcp import types

from context_rail.gateway.mcp_server import build_server
from helpers import actors_for, open_test_session, stdio_session


def add_arguments(parent: str, title: str) -> dict[str, object]:
    return {"preset": "story", "title": title, "rel": [f"part_of={parent}"]}


def call_tool(server: object, name: str, arguments: dict[str, object]) -> types.CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None
    return anyio.run(
        entry.handler,
        None,
        types.CallToolRequestParams(name=name, arguments=arguments),
    )


def test_mcp_pins_the_session_and_ignores_an_actor_argument(role_store) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)
    server = build_server(store=role_store.store, actor="cto", session=token)

    added = call_tool(server, "add", add_arguments(role_store.orders, "MCP pinned"))

    assert added.is_error is False, added.structured_content
    slug = added.structured_content["value"]["slug"]
    assert role_store.orders_seat in actors_for(role_store.store, slug)


def test_mcp_reads_the_session_from_the_environment(role_store) -> None:
    token = open_test_session(role_store.store, role_store.billing_seat)

    async def scenario() -> None:
        async with stdio_session(role_store.store, session=token) as session:
            added = await session.call_tool(
                "add", add_arguments(role_store.billing, "MCP environment")
            )
        assert added.is_error is False, added.structured_content
        slug = added.structured_content["value"]["slug"]
        assert role_store.billing_seat in actors_for(role_store.store, slug)

    anyio.run(scenario)


def test_mcp_without_a_session_in_role_mode_is_read_only(role_store) -> None:
    async def scenario() -> None:
        async with stdio_session(role_store.store) as session:
            read = await session.call_tool("status", {})
            assert read.is_error is False, read.structured_content
            write = await session.call_tool(
                "add", add_arguments(role_store.orders, "MCP unsigned")
            )
        assert write.is_error is True
        assert write.structured_content["error"]["code"] == "SESSION_REQUIRED"
        assert actors_for(role_store.store, "mcp-unsigned") == set()

    anyio.run(scenario)
