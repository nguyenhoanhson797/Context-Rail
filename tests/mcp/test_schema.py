from __future__ import annotations

from context_rail.gateway.mcp_schema import input_schema, read_only_hint, tool_meta
from context_rail.gateway.tools import TOOLS, Param, Tool


def test_every_tool_schema_is_a_closed_object_with_its_required_list() -> None:
    for tool in TOOLS:
        schema = input_schema(tool)
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert schema["required"] == [p.name for p in tool.params if p.required]


def test_param_types_repeat_help_and_defaults_map_exactly() -> None:
    tool = Tool("demo", "x", "write", (
        Param("count", "int", required=True, help="how many"),
        Param("flag", "bool", default=False),
        Param("tag", "str", repeat=True, help="a tag"),
    ), "x:y")
    schema = input_schema(tool)
    assert schema["properties"]["count"] == {"type": "integer", "description": "how many"}
    assert schema["properties"]["flag"] == {"type": "boolean", "default": False}
    assert schema["properties"]["tag"] == {"type": "array", "items": {"type": "string"}, "description": "a tag"}
    assert "enum" not in str(schema)


def test_permission_is_metadata_not_an_input_property() -> None:
    mcp_serve = next(t for t in TOOLS if t.name == "mcp serve")
    assert mcp_serve.permission == "read" and mcp_serve.params == ()
    assert tool_meta(mcp_serve)["context-rail"]["permission"] == "read"
    assert read_only_hint(mcp_serve) is True
    assert "permission" not in input_schema(mcp_serve)["properties"]
