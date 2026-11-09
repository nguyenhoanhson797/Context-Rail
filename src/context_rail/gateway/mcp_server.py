"""The MCP stdio adapter: a view of ``invoke``, generated from the tool table."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from context_rail import __version__
from context_rail.gateway.execution import (
    EXPLICIT_ACTOR,
    resolve_server_actor,
    serving_connection,
)
from context_rail.gateway.gateway import invoke
from context_rail.gateway.mcp_schema import input_schema, read_only_hint, tool_meta
from context_rail.gateway.tools import TOOLS


def build_server(*, store: Path | None, actor: str | None) -> Server:
    """One low-level server whose tools and envelopes come from ``invoke``."""
    pinned_actor = resolve_server_actor(actor)
    by_name = {tool.name: tool for tool in TOOLS}
    write_lock = anyio.Lock()

    async def on_list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.summary,
                    inputSchema=input_schema(tool),
                    annotations=types.ToolAnnotations(
                        **{"readOnlyHint": read_only_hint(tool)}
                    ),
                    **{"_meta": tool_meta(tool)},
                )
                for tool in TOOLS
            ]
        )

    async def on_call_tool(_context: object, params: object) -> types.CallToolResult:
        name = getattr(params, "name", None)
        if not isinstance(name, str) or name not in by_name:
            raise MCPError(
                types.INVALID_PARAMS, f"unknown tool {name!r}"
            )
        arguments = getattr(params, "arguments", None) or {}
        tool = by_name[name]

        async def call() -> dict:
            with serving_connection():
                return await anyio.to_thread.run_sync(
                    lambda: invoke(
                        name, dict(arguments), store=store, actor=pinned_actor
                    )
                )

        # Reads never wait on another call; writes stay serialized so a long
        # mutation cannot interleave with a second one. A cancelled caller leaves
        # its worker thread to finish, and every write is atomic under the store
        # lock, so cancellation never leaves partial store state.
        if tool.permission == "read":
            envelope = await call()
        else:
            async with write_lock:
                envelope = await call()
        text = json.dumps(envelope, ensure_ascii=False)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=envelope,
            isError=not envelope["ok"],
        )

    return Server(
        "context-rail",
        version=__version__,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def run_stdio(store: Path | None, actor: str | None) -> None:
    """Serve one stdio connection until its input closes."""
    explicit = actor if actor is not None else EXPLICIT_ACTOR.get()
    server = build_server(store=store, actor=explicit)

    async def serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(serve)
