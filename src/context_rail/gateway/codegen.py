"""Style presets for ``rail model`` --json-schema: one schema per model.

The schema is derived from ``ProjectModel.as_dict()`` so CLI, MCP, SDK and
UI validate exactly the same IR. It is a read-only view; it never writes.
"""

from __future__ import annotations

from typing import Any

from context_rail.model.compiler import canonical_json


def model_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for ``ProjectModel.as_dict()``."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://context-rail.example/project-model.schema.json",
        "title": "context-rail ProjectModel",
        "description": "The compiled operating model for one pack and its presentation.",
        "type": "object",
        "required": ["version", "pack", "unit", "hierarchy", "focus", "queue", "progress", "execution", "presentation", "digests"],
        "properties": {
            "version": {"type": "integer", "const": 1},
            "pack": {
                "type": "object",
                "required": ["name", "version"],
                "properties": {"name": {"type": "string"}, "version": {"type": "integer"}},
            },
            "unit": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "required": ["preset", "workflow", "label"],
                        "properties": {
                            "preset": {"type": "string"},
                            "workflow": {"type": ["string", "null"]},
                            "label": {"type": ["string", "null"]},
                        },
                    },
                ]
            },
            "hierarchy": {"type": "array", "items": {"type": "object"}},
            "focus": {"type": ["object", "null"]},
            "queue": {"type": "object"},
            "progress": {"type": "object"},
            "execution": {"type": "object"},
            "presentation": {"type": "object"},
            "digests": {"type": "object"},
        },
    }


def model_schema_text() -> str:
    """Canonical JSON of the schema (same canonical form as digests)."""
    return canonical_json(model_json_schema())
