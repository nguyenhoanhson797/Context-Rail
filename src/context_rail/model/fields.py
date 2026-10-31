"""Small shared readers for strict payload fields.

Every reader refuses rather than guessing: a wrong type, an unknown key or an
impossible value raises a ``ModelError`` so the caller can fail closed.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Iterable, NoReturn

from context_rail.model.errors import ModelError

__all__ = [
    "boolean",
    "finite_number",
    "integer",
    "is_number",
    "mapping",
    "no_unknown",
    "refuse",
    "relative_path",
    "require",
    "schema_version",
    "string",
    "string_list",
]

_DRIVE = re.compile(r"^[A-Za-z]:")
_GLOB_CHARS = frozenset("*?[")


def refuse(message: str) -> NoReturn:
    """Refuse a payload as invalid."""
    raise ModelError("VALIDATION_FAILED", message)


def schema_version(data: dict[str, Any], expected: int, what: str) -> int:
    """Return the payload's schema number, refusing every other version."""
    if "schema" not in data:
        refuse(f"{what} is missing schema")
    raw = data["schema"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        refuse(f"{what} schema must be an integer")
    if raw != expected:
        raise ModelError(
            "SCHEMA_UNSUPPORTED",
            f"{what} schema {raw} is not supported; expected {expected}",
        )
    return raw


def mapping(value: Any, what: str) -> dict[str, Any]:
    """Require a mapping with non-empty string keys."""
    if not isinstance(value, dict):
        refuse(f"{what} must be a mapping")
    for key in value:
        if not isinstance(key, str) or not key:
            refuse(f"{what} keys must be non-empty strings")
    return value


def no_unknown(data: dict[str, Any], allowed: Iterable[str], what: str) -> None:
    """Refuse keys outside ``allowed``."""
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        refuse(f"{what} has unknown key(s): " + ", ".join(unknown))


def require(data: dict[str, Any], keys: Iterable[str], what: str) -> None:
    """Refuse when a mandatory key is absent."""
    missing = [key for key in keys if key not in data]
    if missing:
        refuse(f"{what} is missing " + ", ".join(missing))


def string(value: Any, what: str) -> str:
    """Require a non-empty, non-blank string."""
    if not isinstance(value, str):
        refuse(f"{what} must be a string")
    if not value.strip():
        refuse(f"{what} must be a non-empty string")
    return value


def string_list(value: Any, what: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    """Require a list of non-empty strings."""
    if not isinstance(value, list):
        refuse(f"{what} must be a list of strings")
    values = tuple(string(entry, what) for entry in value)
    if not values and not allow_empty:
        refuse(f"{what} must not be empty")
    return values


def boolean(value: Any, what: str) -> bool:
    """Require a real boolean, not a truthy string or number."""
    if not isinstance(value, bool):
        refuse(f"{what} must be true or false")
    return value


def is_number(value: Any) -> bool:
    """A finite ``int`` or ``float``, never a bool or a numeric string."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def finite_number(value: Any, what: str, *, positive: bool = False, minimum: float | None = None) -> Any:
    """Require a finite number within the given bound."""
    if not is_number(value):
        refuse(f"{what} must be a finite number")
    if positive and value <= 0:
        refuse(f"{what} must be positive")
    if minimum is not None and value < minimum:
        refuse(f"{what} must be at least {minimum}")
    return value


def integer(
    value: Any,
    what: str,
    *,
    positive: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Require a real integer within the given bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        refuse(f"{what} must be an integer")
    if positive and value <= 0:
        refuse(f"{what} must be positive")
    if minimum is not None and value < minimum:
        refuse(f"{what} must be at least {minimum}")
    if maximum is not None and value > maximum:
        refuse(f"{what} must be at most {maximum}")
    return value


def relative_path(value: Any, what: str, *, glob: bool = False) -> str:
    """Require a store-relative POSIX path that cannot escape or name a device."""
    text = string(value, what)
    if text.startswith(("/", "\\")) or _DRIVE.match(text):
        refuse(f"{what} must be relative, not {text!r}")
    if "\\" in text or "\x00" in text:
        refuse(f"{what} must be a POSIX path, not {text!r}")
    segments = text.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        refuse(f"{what} must not contain empty, '.' or '..' segments: {text!r}")
    if os.name == "nt" and any(":" in segment for segment in segments[1:]):
        refuse(f"{what} must not name an alternate data stream: {text!r}")
    if not glob and _GLOB_CHARS & set(text):
        refuse(f"{what} must be a plain path, not a glob: {text!r}")
    return text
