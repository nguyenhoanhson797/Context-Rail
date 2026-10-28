"""Shared fixtures for the Context Rail test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.model.pack import Pack, load_pack


@pytest.fixture
def small_pack(tmp_path: Path) -> Pack:
    """The built-in ``small`` pack, loaded for an empty store."""
    return load_pack("small", tmp_path)
