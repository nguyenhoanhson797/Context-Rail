"""Read and tool contracts behind the loopback UI, all through ``invoke``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.context import surface
from context_rail.core.util import now_iso
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.tools import SURFACE_TOOLS, TOOLS, canonical_tool
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack

__all__ = [
    "call_tool",
    "context_document",
    "health_document",
    "reason_document",
    "show_document",
    "surface_document",
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


def surface_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The compiled pack: document types, workflows and declarative verbs."""
    context = _open(store, actor)
    try:
        catalog = surface.catalog(context, role="cto")
        names = sorted({entry["workflow"] for entry in catalog["items"]})
        workflows = []
        for name in names:
            try:
                workflows.append(surface.explain(context, name))
            except surface.SurfaceError:
                continue
        return {
            "ok": True,
            "value": {
                "schema": 1,
                "pack": context.pack.name,
                "presets": catalog["items"],
                "workflows": workflows,
                "actions": surface.verbs(context)["items"],
            },
        }
    finally:
        context.close()


def health_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The doctor's report, run only when the human asks for it."""
    return invoke("doctor", {}, store=store, actor=actor)


def reason_document(store: Path, slug: str, actor: str = "human") -> dict[str, Any]:
    """The proof one item still needs, with the command that produces it."""
    return invoke("why", {"slug": slug}, store=store, actor=actor)


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


def _open(store: Path, actor: str) -> Context:
    """One short-lived context for the read-only surface views."""
    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    return Context(store=store, manifest=manifest, pack=pack, actor=actor)
