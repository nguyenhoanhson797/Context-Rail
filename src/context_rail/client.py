"""A typed Python client for the tool table: CLI, MCP, SDK and UI share it.

The client is generated from ``gateway.tools.TOOLS``; it parses nothing
itself. Every call returns the same Result envelope as ``rail``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import TOOLS

__all__ = ["Client", "model_json_schema", "tools"]


def tools() -> tuple:
    """Every canonical tool (pure data, no handler import)."""
    return TOOLS


def model_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for ``ProjectModel.as_dict()``."""
    from context_rail.gateway.codegen import model_json_schema as schema

    return schema()


class Client:
    """A typed client over the tool table for one store."""

    def __init__(self, store: Path | str | None = None, actor: str | None = None) -> None:
        self.store = Path(store) if store is not None else None
        self.actor = actor

    def call(self, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One tool call; the envelope is the contract."""
        from context_rail.gateway.gateway import try_daemon_invoke

        result = try_daemon_invoke(name, dict(params or {}), store=self.store, actor=self.actor)
        if result is not None:
            return result
        return invoke(name, dict(params or {}), store=self.store, actor=self.actor)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        canonical = name.replace("_", " ")
        if any(tool.name == canonical for tool in TOOLS):

            def _method(*args: Any, **kwargs: Any) -> dict[str, Any]:
                params: dict[str, Any] = {}
                if args and isinstance(args[0], dict):
                    params.update(args[0])
                params.update({k: v for k, v in kwargs.items() if v is not None})
                return self.call(canonical, params)

            return _method
        raise AttributeError(name)


def generate_sdk() -> str:
    """One canonical JSON document naming every tool (stable, sorted)."""
    from context_rail.model.compiler import canonical_json

    payload = {
        "tools": [
            {
                "name": tool.name,
                "summary": tool.summary,
                "permission": tool.permission,
                "handler": tool.handler,
                "decision": tool.decision,
                "params": [
                    {"name": p.name, "type": p.type, "required": p.required, "help": p.help}
                    for p in tool.params
                ],
            }
            for tool in TOOLS
        ],
        "schema": model_json_schema(),
    }
    return canonical_json(payload)
