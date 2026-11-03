"""The JSON contract behind the loopback UI.

The React app copied from context-rail-link speaks the legacy vocabulary:
``/api/context``, ``/api/health``, ``/api/doc/<id>``, ``/api/read/<view>``,
``/api/tool/<name>``, ``/api/tools``, ``/api/projects`` and ``/api/portfolio``.
This module answers each one from the current engine; it never writes a file
itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.core.util import now_iso
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.tools import TOOLS
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.ui import readmodels, snapshot

__all__ = [
    "call_tool",
    "context_document",
    "doc_document",
    "health_document",
    "portfolio_document",
    "projects_document",
    "read_document",
    "tools_document",
]

_REFUSED = frozenset({"mcp serve", "ui serve"})
_RESERVED = frozenset({"actor", "store"})

_ENGINE_KEYS = frozenset(
    {
        "slug",
        "path",
        "primitive",
        "preset",
        "workflow",
        "title",
        "status",
        "category",
        "created",
        "updated",
        "etag",
        "digest",
        "problems",
    }
)


def context_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The pack-aware project snapshot the shell renders."""
    context = _open(store, actor)
    try:
        return snapshot.build(context)
    finally:
        context.close()


def health_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The diagnostics document; the doctor runs behind this read only."""
    result = invoke("doctor", {}, store=store, actor=actor)
    if not result.get("ok"):
        error = result.get("error") or {}
        return {
            "schema": "ui-health-v1",
            "state": "error",
            "as_of": None,
            "doctor": None,
            "cache": None,
            "error": error.get("message") or "the doctor did not run",
            "error_code": error.get("code"),
            "reasons": [],
        }
    value = result["value"]
    counts = value.get("counts") or {}
    return {
        "schema": "ui-health-v1",
        "state": "ok",
        "as_of": now_iso(),
        "doctor": {
            "by_severity": counts,
            "total": sum(int(count) for count in counts.values()),
        },
        "cache": None,
        "error": None,
        "error_code": None,
        "reasons": [],
    }


def doc_document(store: Path, slug: str, actor: str = "human") -> dict[str, Any]:
    """One document plus its body, relations and workflow card."""
    shown = invoke("show", {"slug": slug}, store=store, actor=actor)
    if not shown.get("ok"):
        error = shown.get("error") or {}
        return {
            "status": "error",
            "error": error.get("message") or f"no document {slug!r}",
            "code": error.get("code"),
        }
    value = shown["value"]
    doc = value["doc"]
    frontmatter = {
        key: item for key, item in doc.items() if key not in _ENGINE_KEYS
    }
    frontmatter.update(doc.get("fields") or {})
    frontmatter.setdefault("status", doc.get("status"))
    frontmatter.setdefault("workflow", doc.get("workflow"))
    if doc.get("tags"):
        frontmatter.setdefault("tags", doc["tags"])
    return {
        "status": "ok",
        "doc": {
            "id": doc["slug"],
            "title": doc["title"] or doc["slug"],
            "type": doc.get("preset") or doc.get("primitive"),
            "path": doc["path"],
            "status": doc.get("status"),
            "workflow": doc.get("workflow"),
            "etag": doc.get("etag"),
            "frontmatter": frontmatter,
            "body": value.get("body") or "",
        },
        "card": value.get("card"),
        "out": value.get("out") or {},
        "in": value.get("in") or {},
    }


def read_document(store: Path, view: str, actor: str = "human") -> dict[str, Any]:
    """One named read in the legacy ``{status, ...}`` envelope."""
    context = _open(store, actor)
    try:
        return readmodels.read(context, view)
    finally:
        context.close()


def tools_document() -> dict[str, Any]:
    """The tool table in the shape the command palette and tool form read."""
    tools = []
    for tool in TOOLS:
        if tool.name in _REFUSED:
            continue
        tools.append(
            {
                "name": tool.name,
                "cli_name": tool.name,
                "help": tool.summary,
                "writes": tool.permission != "read",
                "permission": tool.permission,
                "params": [
                    {
                        "name": param.name,
                        "type": "boolean"
                        if param.type == "bool"
                        else "integer"
                        if param.type == "int"
                        else "array"
                        if param.repeat
                        else "string",
                        "required": param.required,
                        "default": param.default,
                        "help": param.help or "",
                        "positional": param.positional,
                        "items": "string" if param.repeat else None,
                    }
                    for param in tool.params
                ],
            }
        )
    return {"status": "ok", "tools": tools, "confirm": []}


def projects_document(store: Path) -> dict[str, Any]:
    """The one store this server serves, in the portfolio's registry shape."""
    name = Path(store).name
    return {
        "status": "ok",
        "current": name,
        "projects": [
            {"id": name, "name": name, "exists": True, "vault": str(store)}
        ],
    }


def portfolio_document(store: Path, actor: str = "human") -> dict[str, Any]:
    """The portfolio read; one store today, so one row."""
    health = health_document(store, actor)
    doctor = health.get("doctor") or {}
    return {
        "status": "ok",
        "projects": [
            {
                "id": Path(store).name,
                "name": Path(store).name,
                "vault": str(store),
                "doctor": doctor,
                "state": health.get("state"),
            }
        ],
    }


def call_tool(
    store: Path, name: str, arguments: dict[str, Any], actor: str = "human"
) -> dict[str, Any]:
    """Run one tool and translate its Result into the page's envelope."""
    from context_rail.store.write import WriteRefused

    if name in _REFUSED:
        raise WriteRefused(
            "FORBIDDEN", f"{name!r} cannot be started over HTTP", {"tool": name}
        )
    if not isinstance(arguments, dict):
        raise WriteRefused("VALIDATION_FAILED", "arguments must be a mapping")
    reserved = sorted(set(arguments) & _RESERVED)
    if reserved:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "the UI never lets a call set " + ", ".join(reserved),
            {"arguments": reserved},
        )
    result = invoke(name, arguments, store=store, actor=actor)
    if result.get("ok"):
        value = result.get("value")
        if isinstance(value, dict):
            payload = {key: item for key, item in value.items() if key != "status"}
            if "status" in value:
                payload["item_status"] = value["status"]
            return {"status": "ok", **payload}
        return {"status": "ok", "value": value}
    error = result.get("error") or {}
    return {
        "status": "error",
        "code": error.get("code"),
        "error": error.get("message") or "the tool refused",
        "details": error.get("details"),
    }


def _open(store: Path, actor: str) -> Context:
    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    return Context(store=store, manifest=manifest, pack=pack, actor=actor)
