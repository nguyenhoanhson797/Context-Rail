"""Build-wide defaults that every layer can import without pulling in YAML."""

from __future__ import annotations

__all__ = ["DEFAULT_PACK"]

#: The built-in pack a new store selects.
DEFAULT_PACK = "basic"
