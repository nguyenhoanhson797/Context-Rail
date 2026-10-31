"""The MCP JSON schema a tool table entry renders, with no SDK import."""

from __future__ import annotations

from context_rail.gateway.tools import Tool

_TYPES = {"str": "string", "int": "integer", "bool": "boolean"}


def input_schema(tool: Tool) -> dict:
    """A closed object schema for one tool's finite parameters."""
    properties: dict[str, dict] = {}
    required: list[str] = []
    for param in tool.params:
        kind = _TYPES[param.type]
        entry: dict = {"type": kind}
        if param.repeat:
            entry = {"type": "array", "items": {"type": kind}}
        if param.help:
            entry["description"] = param.help
        if param.default is not None:
            entry["default"] = param.default
        properties[param.name] = entry
        if param.required:
            required.append(param.name)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def tool_meta(tool: Tool) -> dict:
    """Context Rail's permission and decision class as tool metadata."""
    return {
        "context-rail": {
            "permission": tool.permission,
            "decision": tool.decision,
        }
    }


def read_only_hint(tool: Tool) -> bool:
    """Whether the tool only reads the store."""
    return tool.permission == "read"
