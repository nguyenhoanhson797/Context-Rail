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
    """One command: its permission, its decision class, its handler."""

    name: str
    summary: str
    permission: str
    params: tuple[Param, ...]
    handler: str
    decision: str = "cto-alone"


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="init",
        summary="Create a store, or turn an interview or answers file into the project picture.",
        permission="write",
        params=(
            Param("dir", "str", help="project directory (default: the current one)"),
            Param("pack", "str", default="small", help="pack name"),
            Param("root", "str", default="context-rail", help="store folder inside the project"),
            Param("interview", "bool", help="ask the picture questions in a terminal"),
            Param("answers_file", "str", help="JSON answers written by the human"),
            Param("draft_file", "str", help="JSON picture proposed by the CTO"),
            Param("confirm", "str", help="the typed phrase APPROVE charter"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.onboarding:init",
    ),
    Tool(
        name="approve",
        summary="Record the human's approval of the protected picture's meaning.",
        permission="human",
        params=(
            Param("target", "str", required=True, positional=True, help="charter, a protected slug or pack:<name>"),
            Param("confirm", "str", required=True, help="the typed phrase APPROVE <target>"),
            Param("confirmer", "str", required=True, help="who approved it"),
            Param("expect", "str", help="the snapshot_digest the human saw"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.onboarding:approve",
        decision="human-only",
    ),
    Tool(
        name="check",
        summary="Create or edit one Check document and declare what it proves.",
        permission="write",
        params=(
            Param("slug", "str", required=True, positional=True, help="check slug"),
            Param("target", "str", help="the Item slug this check verifies"),
            Param("spec_file", "str", required=True, help="strict JSON check spec"),
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
            Param("expect", "str", required=True, help="current etag, or absent on create"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.checks:check",
    ),
    Tool(
        name="verify",
        summary="Run a check, or every executable check an Item declares, and record verdicts.",
        permission="write",
        params=(
            Param("target", "str", required=True, positional=True, help="check or Item slug"),
            Param("worktree", "str", help="the project checkout or a dispatch worktree"),
            Param("verdict", "str", help="pass or fail, for a human check"),
            Param("evidence", "str", repeat=True, help="evidence Record id"),
            Param("confirm", "str", help="the typed phrase PASS or FAIL <check>"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.checks:verify",
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
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
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
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
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
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
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
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.docs:link",
    ),
    Tool(
        name="rename",
        summary="Rename a document's slug and retarget every link to it.",
        permission="write",
        params=(
            Param("old", "str", required=True, positional=True, help="current slug"),
            Param("new", "str", required=True, positional=True, help="new slug"),
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.reshape:rename",
    ),
    Tool(
        name="relocate",
        summary="Move every document to the path its placement renders now.",
        permission="write",
        params=(
            Param("dry_run", "bool", help="report the moves without applying them"),
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.reshape:relocate",
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
    Tool(
        name="doctor",
        summary="Report store integrity issues, optionally repairing interrupted writes.",
        permission="read",
        params=(
            Param("repair", "bool", help="roll interrupted writes forward first"),
        ),
        handler="context_rail.gateway.handlers.doctor:doctor",
    ),
)
