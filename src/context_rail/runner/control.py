"""One reader/writer pair and one binding schema for local control files.

Progress and key-binding files live under ``local_dir`` so no control state
lands in the repository tree or the store. Every reader applies the same rule:
a file that cannot be read is reported as ``corrupt`` with its identity, never
as absent proof. Writers replace atomically so a crash leaves either the old
file or the new one, never a partial document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = [
    "CONTROL_SCHEMA",
    "binding_payload",
    "merge_control",
    "read_control",
    "write_control",
]

CONTROL_SCHEMA = 1


def read_control(
    path: Path, *, identity: str, key: str
) -> dict[str, Any] | None:
    """The control object at ``path``; None when absent, ``corrupt`` when unreadable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {key: identity, "corrupt": True}
    if not isinstance(payload, dict):
        return {key: identity, "corrupt": True}
    return payload


def write_control(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Write one control object atomically and return it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return payload


def merge_control(
    path: Path,
    *,
    identity: str,
    key: str,
    values: dict[str, Any],
    schema: int = CONTROL_SCHEMA,
) -> dict[str, Any]:
    """Merge ``values`` into one control object, preserving the shared schema."""
    current = read_control(path, identity=identity, key=key) or {}
    if current.get("corrupt"):
        current = {key: identity}
    merged = {**current, **values, "schema": schema, key: identity}
    merged.pop("corrupt", None)
    return write_control(path, merged)


def binding_payload(
    key: str, values: dict[str, Any], *, schema: int = CONTROL_SCHEMA
) -> dict[str, Any]:
    """The one shape every key binding uses."""
    return {**values, "schema": schema, "key": key}
