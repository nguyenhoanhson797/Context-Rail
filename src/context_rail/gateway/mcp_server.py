"""The MCP stdio adapter: a view of ``invoke``, generated from the tool table."""

from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from context_rail import __version__
from context_rail.core.result import fail
from context_rail.gateway.execution import (
    EXPLICIT_ACTOR,
    caller_identity,
    resolve_server_actor,
    serving_connection,
)
from context_rail.gateway.gateway import _open_context, invoke
from context_rail.gateway.identity import resolve_identity, roles_declared
from context_rail.gateway.mcp_schema import input_schema, read_only_hint, tool_meta
from context_rail.gateway.role_guard import tools_for
from context_rail.gateway.tools import TOOLS

_SESSION_COMMAND = "rail seat open <seat> --print-env"


def build_server(
    *, store: Path | None, actor: str | None, session: str | None = None
) -> Server:
    """One low-level server whose tools and envelopes come from ``invoke``.

    The server is pinned to a session (``RAIL_SESSION`` unless given) and sends
    it with every call. A store that declares roles and has no session is read
    only, and the actor name is never sent: role mode refuses one.
    """
    pinned_session = session if session is not None else _environment_session()
    scoped = roles_declared(store)
    pinned_actor = (
        None if pinned_session is not None or scoped else resolve_server_actor(actor)
    )
    by_name = {tool.name: tool for tool in TOOLS}
    write_lock = anyio.Lock()

    def listed() -> frozenset[str]:
        """The tool names this pinned caller may see, or none when it cannot be placed."""
        if not scoped:
            return frozenset(tool.name for tool in TOOLS)
        try:
            context = _open_context(
                store, pinned_actor or "human", pinned_actor is not None
            )
        except Exception:
            return frozenset()
        try:
            context.identity = resolve_identity(
                context, actor=None, session=pinned_session, tty=False
            )
            return tools_for(context)
        except Exception:
            return frozenset()
        finally:
            context.close()

    async def on_list_tools(_context: object, _params: object) -> types.ListToolsResult:
        names = listed()
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
                if tool.name in names
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
            if scoped and pinned_session is None and tool.permission != "read":
                return fail(
                    "SESSION_REQUIRED",
                    "this MCP server is not pinned to a seat session; "
                    "open one with 'rail seat open'",
                    {"reason": "session_required", "command": _SESSION_COMMAND},
                )
            with caller_identity(pinned_actor, None, session=pinned_session):
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


def _environment_session() -> str | None:
    """The session token this server was started with, when it carries one."""
    token = os.environ.get("RAIL_SESSION")
    return token.strip() if isinstance(token, str) and token.strip() else None


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
