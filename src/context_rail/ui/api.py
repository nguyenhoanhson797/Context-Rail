"""Read and tool contracts behind the loopback UI, all through ``invoke``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.core.util import now_iso
from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import SURFACE_TOOLS, TOOLS, canonical_tool

__all__ = [
    "call_tool",
    "context_document",
    "show_document",
    "tool_catalog",
]

_REFUSED = frozenset({"mcp serve", "ui serve"})
_RESERVED = frozenset({"actor", "store"})


def context_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The first screen: status plus the first page of items, never doctor."""
    status = invoke("status", {}, store=store, actor=actor)
    if not status.get("ok"):
        return status
    found = invoke("find", {"primitive": "item", "limit": 50}, store=store, actor=actor)
    if not found.get("ok"):
        return found
    return {
        "ok": True,
        "value": {
            "schema": 1,
            "status": status["value"],
            "items": found["value"]["items"],
            "total": found["value"]["total"],
            "omitted": found["value"]["omitted"],
            "generated_at": now_iso(),
        },
    }


def show_document(store: Path, slug: str, actor: str = "human") -> dict[str, Any]:
    return invoke("show", {"slug": slug}, store=store, actor=actor)


def tool_catalog() -> dict[str, Any]:
    return {
        "ok": True,
        "value": [
            {
                "name": tool.name,
                "summary": tool.summary,
                "permission": tool.permission,
                "params": [
                    {
                        "name": param.name,
                        "type": param.type,
                        "required": param.required,
                        "repeat": param.repeat,
                        "default": param.default,
                        "help": param.help,
                    }
                    for param in tool.params
                ],
            }
            for tool in SURFACE_TOOLS
            if not tool.hidden
        ],
    }


def call_tool(
    store: Path, name: str, arguments: dict[str, Any], actor: str = "human"
) -> dict[str, Any]:
    from context_rail.store.write import WriteRefused

    if canonical_tool(name) in _REFUSED:
        raise WriteRefused(
            "FORBIDDEN", f"{name!r} cannot be started over HTTP", {"tool": name}
        )
    if canonical_tool(name) not in {tool.name for tool in TOOLS}:
        raise WriteRefused("NOT_FOUND", f"unknown tool {name!r}", {"tool": name})
    if not isinstance(arguments, dict):
        raise WriteRefused("VALIDATION_FAILED", "arguments must be a mapping")
    reserved = sorted(set(arguments) & _RESERVED)
    if reserved:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "the UI never lets a call set " + ", ".join(reserved),
            {"arguments": reserved},
        )
    return invoke(name, arguments, store=store, actor=actor)
