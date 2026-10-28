"""Decision rights: which actor may use which permission."""

from __future__ import annotations

import pytest

from context_rail.kernel.rights import PERMISSIONS, allowed


def test_human_permission_refuses_cto():
    assert allowed("human", "cto") is not None and allowed("human", "human") is None


def test_stage_1a_opens_read_and_write():
    assert allowed("write", "cto") is None
    assert allowed("read", "human") is None
    assert allowed("read", "cto") is None
    assert allowed("write", "implementer") is None


def test_refusal_is_a_reason_string():
    reason = allowed("human", "cto")
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("permission", ["", "admin", "Human", "read ", None])
def test_unknown_permission_is_refused(permission):
    reason = allowed(permission, "human")
    assert isinstance(reason, str) and reason


def test_permissions_are_the_three_declared_kinds():
    assert PERMISSIONS == ("read", "write", "human")
