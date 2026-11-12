"""A seat brief can list the candidates recorded for its anchor item."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import as_human, as_seat, edit_roles, write_candidate


def _grant(store, anchor: str) -> None:
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "impl-1", "role": "implementer", "anchor": anchor},
            store=store,
            actor=None,
        )
    assert is_ok(granted), granted


def _with_candidates_part(roles: dict[str, Any]) -> None:
    parts = roles["implementer"]["sees"]["parts"]
    if "candidates" not in parts:
        parts.append("candidates")


def test_the_candidates_part_lists_recorded_commits(role_store: SimpleNamespace) -> None:
    edit_roles(role_store.store, _with_candidates_part)
    _grant(role_store.store, role_store.o1)
    write_candidate(role_store.store, "01JBRIEFCAND0000000000001", role_store.o1, "ab" * 20)

    brief = as_seat(role_store.store, "impl-1", "cto brief", {})

    assert is_ok(brief), brief
    assert "candidates" in brief["value"]["parts"]
    assert "attempt 1 " + "ab" * 6 in brief["value"]["text"]


def test_the_candidates_part_is_empty_without_a_candidate(
    role_store: SimpleNamespace,
) -> None:
    edit_roles(role_store.store, _with_candidates_part)
    _grant(role_store.store, role_store.o1)

    brief = as_seat(role_store.store, "impl-1", "cto brief", {})

    assert is_ok(brief), brief
    assert "attempt 1" not in brief["value"]["text"]
