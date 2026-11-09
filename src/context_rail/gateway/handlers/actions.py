"""Run a declarative pack action through the same gateway the caller uses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from context_rail.core.util import sha256_hex
from context_rail.gateway.handlers.common import record_data
from context_rail.store import query
from context_rail.store.write import WriteRefused, commit, replay

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["do"]

_RESERVED_PARAMS = frozenset({"path", "actor", "key", "profile", "store", "worktree"})


def do(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Check one action's preconditions, call its tool, and record the verb."""
    name = args["action"]
    action = ctx.pack.action(name)
    item = (args.get("item") or "").strip()
    params = _params(args["set"], item=item, etag=args.get("etag"))
    from context_rail.gateway.tools import TOOLS

    spec = next((tool for tool in TOOLS if tool.name == action.tool), None)
    read_only = spec is not None and spec.permission == "read"
    tool_key = ""
    action_key = ""
    if not read_only:
        tool_key = _tool_key(name, action.version, item, params, args.get("key"))
        action_key = "action-" + sha256_hex(tool_key.encode("utf-8"))[:26]
        saved = replay(ctx.store, action_key)
        if saved is not None:
            data = record_data(ctx.index, saved.get("record"))
            return {
                "action": name,
                "version": action.version,
                "result": data.get("result"),
                "record": saved.get("record"),
            }

    if action.on != "*":
        doc = _item(ctx, item)
        if doc["preset"] != action.on:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"action {name!r} applies to a {action.on!r}, not a {doc['preset']!r}",
                {"action": name, "on": action.on, "preset": doc["preset"]},
            )
    if action.from_states:
        doc = _item(ctx, item)
        if doc["status"] not in action.from_states:
            raise WriteRefused(
                "INVALID_STATE",
                f"action {name!r} starts from "
                + ", ".join(action.from_states)
                + f", not {doc['status']!r}",
                {
                    "action": name,
                    "from": list(action.from_states),
                    "status": doc["status"],
                },
            )
    _check_requires(ctx, action, item)
    tool_args = _tool_args(action.args, params)
    if tool_key:
        tool_args.setdefault("key", tool_key)

    from context_rail.gateway.gateway import invoke

    result = invoke(action.tool, tool_args, store=ctx.store, actor=actor)
    if not result.get("ok"):
        error = result.get("error") or {}
        raise WriteRefused(
            error.get("code") or "BUSINESS_RULE_VIOLATED",
            error.get("message") or f"action {name!r} failed",
            {"action": name, **(error.get("details") or {})},
        )
    value = result.get("value")
    if read_only:
        return {"action": name, "version": action.version, "result": value, "record": None}
    record = commit(
        ctx.store,
        ctx.manifest,
        [],
        {
            "kind": "action.executed",
            "about": [item] if item else [],
            "data": {
                "schema": 1,
                "action": name,
                "version": action.version,
                "tool": action.tool,
                "args": tool_args,
                "result": value,
            },
        },
        actor=actor,
        key=action_key,
        index=ctx.index,
    )
    return {
        "action": name,
        "version": action.version,
        "result": value,
        "record": record.get("record"),
    }


def _item(ctx: Context, item: str) -> dict[str, Any]:
    if not item:
        raise WriteRefused(
            "VALIDATION_FAILED", "this action needs --item", {"argument": "item"}
        )
    row = ctx.index.conn.execute(
        "SELECT slug, preset, status FROM docs WHERE slug = ? ORDER BY path LIMIT 1",
        (item,),
    ).fetchone()
    if row is None:
        raise WriteRefused("NOT_FOUND", f"no item {item!r}", {"item": item})
    return {"slug": row[0], "preset": row[1], "status": row[2]}


def _params(entries: list[str], *, item: str, etag: str | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if item:
        params["item"] = item
    if etag:
        params["etag"] = etag
    for entry in entries:
        name, separator, value = entry.partition("=")
        name, value = name.strip(), value.strip()
        if not name or not separator:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{entry!r} is not NAME=VALUE", {"argument": entry}
            )
        if name in _RESERVED_PARAMS:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"an action may not set {name!r}",
                {"argument": name},
            )
        params[name] = value
    return params


def _tool_args(template: dict[str, Any], params: dict[str, str]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in template.items():
        if isinstance(value, str):
            resolved[key] = _render(value, params)
        elif isinstance(value, (list, tuple)):
            resolved[key] = [_render(item, params) for item in value]
        else:
            resolved[key] = value
    return resolved


def _render(text: str, params: dict[str, str]) -> str:
    resolved = text
    for name in _placeholders(text):
        if name not in params:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"the action needs --set {name}=<value>",
                {"argument": name},
            )
        resolved = resolved.replace("{" + name + "}", params[name])
    return resolved


def _placeholders(text: str) -> list[str]:
    found: list[str] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return found
        end = text.find("}", start + 1)
        if end < 0:
            return found
        found.append(text[start + 1 : end])
        index = end + 1


def _check_requires(ctx: Context, action: Any, item: str) -> None:
    requires = action.requires
    if requires.children:
        children = ctx.index.conn.execute(
            "SELECT d.slug, d.category FROM relations r JOIN docs d ON d.path = r.src_path "
            "WHERE r.dst = ? AND r.rel = 'part_of' ORDER BY d.slug",
            (item,),
        ).fetchall()
        open_children = [
            slug for slug, category in children if category not in ("done", "dropped")
        ]
        if open_children:
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                "child item(s) are still open: " + ", ".join(open_children),
                {
                    "action": action.name,
                    "condition": "children",
                    "open": open_children,
                    "command": f"rail read show {open_children[0]}",
                },
            )
    if requires.check:
        row = query.check_state(ctx.index, requires.check)
        if row is None or not row.get("passed"):
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                f"check {requires.check!r} has no passing proof",
                {
                    "action": action.name,
                    "condition": "check",
                    "check": requires.check,
                    "command": (
                        f"rail check {requires.check} --target {item} "
                        "--spec-file <file> --expect absent"
                    ),
                },
            )
    if requires.review:
        review = query.review_for_item(ctx.index, item)
        verdict = (review or {}).get("verdict")
        if verdict != "approve":
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                "the item has no approving review",
                {
                    "action": action.name,
                    "condition": "review",
                    "command": f"rail review {item} --run <run-id> --key <key>",
                },
            )
    if requires.field:
        present = ctx.index.conn.execute(
            "SELECT 1 FROM fields WHERE path IN "
            "(SELECT path FROM docs WHERE slug = ?) AND key = ? LIMIT 1",
            (item, requires.field),
        ).fetchone()
        if present is None:
            raise WriteRefused(
                "BUSINESS_RULE_VIOLATED",
                f"the item has no {requires.field!r} field",
                {
                    "action": action.name,
                    "condition": "field",
                    "field": requires.field,
                    "command": (
                        f"rail change update {item} --set {requires.field}=<value> "
                        "--expect <etag>"
                    ),
                },
            )


def _tool_key(
    name: str,
    version: int,
    item: str,
    params: dict[str, str],
    caller_key: str | None,
) -> str:
    body = json.dumps([name, version, item, params, caller_key], sort_keys=True)
    return "action-" + sha256_hex(body.encode("utf-8"))[:26]
