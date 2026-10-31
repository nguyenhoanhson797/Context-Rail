"""The one tool table: every surface (CLI, MCP, UI) is generated from it.

This module is pure data. The CLI imports it at startup, so it must not import
YAML, SQLite or any handler; handlers are ``"module:function"`` strings that are
imported only when a tool is invoked.
"""

from __future__ import annotations

from typing import Any, NamedTuple

__all__ = ["TOOLS", "Param", "Tool"]


class Param(NamedTuple):
    """One declared argument. ``type`` is ``str``, ``int`` or ``bool``."""

    name: str
    type: str
    required: bool = False
    default: Any = None
    help: str = ""
    positional: bool = False
    repeat: bool = False


class Tool(NamedTuple):
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
        name="migrate",
        summary="Import a v1/v2 legacy store into a fresh v3 store in one commit.",
        permission="human",
        params=(
            Param("source", "str", required=True, help="v1 or v2 legacy store folder"),
            Param("dir", "str", required=True, help="project directory for the new store"),
            Param("root", "str", default="context-rail", help="store folder inside the project"),
            Param("pack", "str", default="small", help="pack name"),
            Param("dry_run", "bool", default=False, help="plan without writing"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.migrate:migrate",
        decision="human-only",
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
        name="dispatch",
        summary="Run one implementer in an isolated worktree and verify its candidate.",
        permission="write",
        params=(
            Param("item", "str", required=True, positional=True, help="leaf Item slug"),
            Param("profile", "str", default="implementer", help="implementer profile name"),
            Param("expect", "str", required=True, help="the Item's current etag"),
            Param("key", "str", required=True, help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.dispatch:dispatch",
    ),
    Tool(
        name="review",
        summary="Review one verified candidate in its own worktree and record the verdict.",
        permission="write",
        params=(
            Param("item", "str", required=True, positional=True, help="leaf Item slug"),
            Param("run", "str", required=True, help="the verified implementer run id"),
            Param("profile", "str", default="reviewer", help="reviewer profile name"),
            Param("key", "str", required=True, help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.dispatch:review",
    ),
    Tool(
        name="merge",
        summary="Rebase an approved candidate onto the default branch and fast-forward it.",
        permission="write",
        params=(
            Param("item", "str", required=True, positional=True, help="leaf Item slug"),
            Param("run", "str", required=True, help="the verified implementer run id"),
            Param("expect", "str", required=True, help="the Item's current etag"),
            Param("push", "bool", help="push the default branch when the charter allows it"),
            Param("key", "str", required=True, help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.dispatch:merge",
    ),
    Tool(
        name="report",
        summary="File the one structured report a completed run owes.",
        permission="write",
        params=(
            Param("item", "str", positional=True, help="the Item the run worked on"),
            Param("run", "str", required=True, help="the run's Record id"),
            Param("report_file", "str", required=True, help="strict JSON report file"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.reports:report",
    ),
    Tool(
        name="triage",
        summary="Cluster untriaged reports into fixes, proposals or engine issues.",
        permission="write",
        params=(
            Param("limit", "int", default=100, help="reports to consider (1-1000)"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.reports:triage",
    ),
    Tool(
        name="cto brief",
        summary="Assemble the bounded CTO brief from the indexed project state.",
        permission="read",
        params=(
            Param("max_chars", "int", default=12000, help="character budget (100-32000)"),
            Param("item_limit", "int", default=20, help="entries per section (1-100)"),
        ),
        handler="context_rail.gateway.handlers.cto:brief",
    ),
    Tool(
        name="cto tick",
        summary="Launch one bounded CTO droid session behind the single-instance lock.",
        permission="write",
        params=(
            Param("key", "str", required=True, help="idempotency key"),
            Param("timeout", "int", help="seconds for the child (default: the CTO profile's)"),
        ),
        handler="context_rail.gateway.handlers.cto:tick",
    ),
    Tool(
        name="digest",
        summary="Summarize activity, waits, reversible decisions, stuck and too-long items.",
        permission="read",
        params=(
            Param("since", "str", help="UTC timestamp: activity from then on"),
            Param("limit", "int", default=20, help="rows per list (1-100)"),
            Param("max_chars", "int", default=12000, help="summary budget (100-32000)"),
        ),
        handler="context_rail.gateway.handlers.views:digest",
    ),
    Tool(
        name="ledger",
        summary="List runs with attempt-counted usage and unknown counters kept apart.",
        permission="read",
        params=(
            Param("item", "str", help="Item slug"),
            Param("profile", "str", help="profile name"),
            Param("since", "str", help="UTC timestamp: runs that ended from then on"),
            Param("limit", "int", default=20, help="rows (1-100)"),
        ),
        handler="context_rail.gateway.handlers.views:ledger",
    ),
    Tool(
        name="adapters sync",
        summary="Write the managed AGENTS.md block and one .agents skill file per skill Note.",
        permission="write",
        params=(
            Param("dry_run", "bool", help="report the targets without writing them"),
            Param("force", "bool", help="replace a hand-edited block (human only)"),
            Param("confirm", "str", help="the typed phrase FORCE ADAPTERS adapters"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.adapters:sync",
    ),
    Tool(
        name="pack list",
        summary="List installed packs with their origin and the one active pack.",
        permission="read",
        params=(),
        handler="context_rail.gateway.handlers.packs:pack_list",
    ),
    Tool(
        name="pack show",
        summary="Show one pack's resources, origin, etag and approval.",
        permission="read",
        params=(Param("name", "str", required=True, help="pack name"),),
        handler="context_rail.gateway.handlers.packs:pack_show",
    ),
    Tool(
        name="pack validate",
        summary="Report whether one pack is complete and valid, never refusing.",
        permission="read",
        params=(Param("name", "str", required=True, help="pack name"),),
        handler="context_rail.gateway.handlers.packs:pack_validate",
    ),
    Tool(
        name="pack create",
        summary="Copy a pack into an unselected draft without changing rail.yaml.",
        permission="write",
        params=(
            Param("name", "str", required=True, help="new pack name"),
            Param("from", "str", default="small", help="pack to copy"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.packs:pack_create",
    ),
    Tool(
        name="pack use",
        summary="Switch the selected pack in one approval-guarded commit.",
        permission="human",
        params=(
            Param("name", "str", required=True, help="pack to select"),
            Param("expect", "str", required=True, help="current rail.yaml etag"),
            Param("map", "str", repeat=True, help="status map entry as OLD=NEW"),
            Param("dry_run", "bool", default=False, help="plan without writing"),
            Param("confirm", "str", help="the typed phrase USE PACK <name>"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.packs:pack_use",
        decision="human-only",
    ),
    Tool(
        name="measure versions",
        summary="Compare two versions of a skill or profile from indexed run facts.",
        permission="read",
        params=(
            Param("target_class", "str", required=True, help="skill or profile"),
            Param("target", "str", required=True, help="skill path or profile name"),
            Param("before", "int", required=True, help="the older version"),
            Param("after", "int", required=True, help="the newer version"),
            Param("since", "str", help="UTC timestamp: runs from then on"),
            Param("until", "str", help="UTC timestamp: runs until then"),
        ),
        handler="context_rail.gateway.handlers.measure:measure_versions",
    ),
    Tool(
        name="measure fix",
        summary="Report whether a resolved fix's reports recurred after its boundary.",
        permission="read",
        params=(
            Param("item", "str", required=True, help="the fix Item slug"),
            Param("until", "str", help="UTC timestamp: reports until then"),
        ),
        handler="context_rail.gateway.handlers.measure:measure_fix",
    ),
    Tool(
        name="rollback",
        summary="Restore a retained skill or profile version into a new version.",
        permission="write",
        params=(
            Param("target_class", "str", required=True, help="skill or profile"),
            Param("target", "str", required=True, help="workspace-relative target"),
            Param("version", "int", required=True, help="version to restore"),
            Param("expect", "str", required=True, help="the current etag"),
            Param("key", "str", help="idempotency key"),
            Param("confirm", "str", help="the typed phrase APPROVE <slug>"),
        ),
        handler="context_rail.gateway.handlers.versions:rollback",
        decision="cto-alone",
    ),
    Tool(
        name="mcp serve",
        summary="Serve the tool table over one MCP stdio connection.",
        permission="read",
        params=(),
        handler="context_rail.gateway.handlers.mcp:serve",
    ),
    Tool(
        name="sync driver",
        summary="Merge one scratch file for Git without touching the live store.",
        permission="write",
        params=(
            Param("kind", "str", required=True, positional=True, help="doc, record or config"),
            Param("base", "str", required=True, positional=True, help="the merge base file"),
            Param("ours", "str", required=True, positional=True, help="the file Git writes %A into"),
            Param("theirs", "str", required=True, positional=True, help="the incoming file"),
            Param("path", "str", required=True, positional=True, help="the store-relative path"),
        ),
        handler="context_rail.gateway.handlers.sync:sync_driver",
        decision="cto-alone",
    ),
    Tool(
        name="sync",
        summary="Integrate and publish store-only clone changes, or install drivers.",
        permission="write",
        params=(
            Param("install", "bool", help="write .gitattributes and clone-local drivers"),
            Param("remote", "str", help="remote name (default: the upstream)"),
            Param("branch", "str", help="branch name (default: the upstream)"),
            Param("key", "str", help="idempotency key"),
        ),
        handler="context_rail.gateway.handlers.sync:sync",
        decision="cto-alone",
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
            Param("reason", "str", help="why this reversible decision was made"),
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
    Tool(
        name="ci",
        summary="Gate the store: doctor issues plus approval and done gates, never a Check run.",
        permission="read",
        params=(
            Param("warnings_as_errors", "bool", help="fail on warnings too"),
        ),
        handler="context_rail.gateway.handlers.maintenance:ci",
    ),
)
