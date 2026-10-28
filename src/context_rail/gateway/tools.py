"""The one tool table: every surface (CLI, MCP, UI) is generated from it.

This module is pure data. The CLI imports it at startup, so it must not import
YAML, SQLite or any handler; handlers are ``"module:function"`` strings that are
imported only when a tool is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["TOOLS", "Param", "Tool"]


@dataclass(frozen=True)
class Param:
    """One declared argument. ``type`` is ``str``, ``int`` or ``bool``."""

    name: str
    type: str
    required: bool = False
    default: Any = None
    help: str = ""
    positional: bool = False
    repeat: bool = False


@dataclass(frozen=True)
class Tool:
    """One command: its permission, its arguments and its lazy handler."""

    name: str
    summary: str
    permission: str
    params: tuple[Param, ...]
    handler: str


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="init",
        summary="Create a store's pinned layout in a project directory.",
        permission="write",
        params=(
            Param("dir", "str", help="project directory (default: the current one)"),
            Param("pack", "str", default="small", help="pack name"),
            Param("root", "str", default="context-rail", help="store folder inside the project"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.init:init",
    ),
    Tool(
        name="add",
        summary="Create one document from a preset.",
        permission="write",
        params=(
            Param("preset", "str", required=True, positional=True, help="preset name"),
            Param("title", "str", required=True, positional=True, help="document title"),
            Param("rel", "str", repeat=True, help="relation as REL=SLUG"),
            Param("tag", "str", repeat=True, help="tag to add"),
            Param("set", "str", repeat=True, help="frontmatter field as KEY=VALUE"),
            Param("body_file", "str", help="file whose text becomes the body"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.docs:add",
    ),
    Tool(
        name="update",
        summary="Change a document's fields, tags or body.",
        permission="write",
        params=(
            Param("slug", "str", required=True, positional=True, help="document slug"),
            Param("expect", "str", required=True, help="current etag"),
            Param("set", "str", repeat=True, help="frontmatter field as KEY=VALUE"),
            Param("tag", "str", repeat=True, help="tag edit as +TAG or -TAG"),
            Param("body_file", "str", help="file whose text replaces the body"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.docs:update",
    ),
    Tool(
        name="move",
        summary="Move an item through its lifecycle.",
        permission="write",
        params=(
            Param("slug", "str", required=True, positional=True, help="item slug"),
            Param("status", "str", required=True, positional=True, help="target status"),
            Param("expect", "str", required=True, help="current etag"),
            Param("resolution", "str", help="how the item was resolved"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.docs:move",
    ),
    Tool(
        name="link",
        summary="Add or remove one relation on a document.",
        permission="write",
        params=(
            Param("src", "str", required=True, positional=True, help="source slug"),
            Param("rel", "str", required=True, positional=True, help="relation name"),
            Param("dst", "str", required=True, positional=True, help="target slug"),
            Param("expect", "str", required=True, help="current source etag"),
            Param("remove", "bool", help="remove the relation instead of adding it"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.docs:link",
    ),
    Tool(
        name="find",
        summary="List documents matching every supplied filter.",
        permission="read",
        params=(
            Param("primitive", "str", help="primitive name"),
            Param("preset", "str", help="preset name"),
            Param("tag", "str", repeat=True, help="required tag"),
            Param("status", "str", help="status name"),
            Param("category", "str", help="category name"),
            Param("text", "str", help="full-text query"),
            Param("rel", "str", help="relation filter as REL=SLUG"),
            Param("limit", "int", default=50, help="page size (1-100)"),
        ),
        handler="context_rail.gateway.handlers.views:find",
    ),
    Tool(
        name="show",
        summary="Show one document with its relations and body.",
        permission="read",
        params=(
            Param("slug", "str", required=True, positional=True, help="document slug"),
            Param("body_chars", "int", default=4000, help="body characters to return"),
        ),
        handler="context_rail.gateway.handlers.views:show",
    ),
    Tool(
        name="status",
        summary="Counts, executable items, blockers and stuck items.",
        permission="read",
        params=(),
        handler="context_rail.gateway.handlers.views:status",
    ),
    Tool(
        name="reindex",
        summary="Sync or rebuild the store index and report what changed.",
        permission="read",
        params=(Param("full", "bool", help="rebuild from scratch"),),
        handler="context_rail.gateway.handlers.maintenance:reindex",
    ),
)
