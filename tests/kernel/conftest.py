"""The default charter the kernel rules are tested against."""

from __future__ import annotations

import pytest

from context_rail.model.policy import Charter, parse_charter

DEFAULT_CHARTER_FIELDS = {
    "schema": 1,
    "project": {"goal": "ride", "vision": "v", "scope": ["must-1", "must-2"], "wont": []},
    "default_branch": "main",
}


@pytest.fixture
def policy() -> Charter:
    return parse_charter(dict(DEFAULT_CHARTER_FIELDS))
