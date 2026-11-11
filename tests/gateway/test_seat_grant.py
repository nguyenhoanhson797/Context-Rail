"""Granting seats: the human bootstraps, a seat delegates inside its reach."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def roles_of(store: Path) -> dict[str, Any]:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document["operating"]["roles"]


def edit_roles(store: Path, change: Any) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document["operating"]["roles"])
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )


def add_human_role(store: Path) -> None:
    def change(roles: dict[str, Any]) -> None:
        roles["human-lead"] = {
            "anchor": "project",
            "reach": {"read": ["project"], "write": ["project"]},
            "can": {"grant": []},
            "human": True,
            "workspace": "none",
        }
        roles["lead"]["can"]["grant"].append("human-lead")

    edit_roles(store, change)


def grant(
    store: Path,
    seat: str,
    role: str,
    *,
    anchor: str = "project",
    profile: str | None = None,
    as_seat: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {"seat": seat, "role": role, "anchor": anchor}
    if profile is not None:
        args["profile"] = profile
    if as_seat is None:
        with as_human():
            return invoke("seat grant", args, store=store, actor=None)
    token = open_test_session(store, as_seat)
    with caller_identity(None, None, session=token):
        return invoke("seat grant", args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def seats(store: Path) -> dict[str, dict[str, Any]]:
    with as_human():
        listed = invoke("seat list", {}, store=store, actor=None)
    assert is_ok(listed), listed
    return {row["seat"]: row for row in listed["value"]["seats"]}


def test_the_human_bootstraps_the_first_lead_seat(role_store: SimpleNamespace) -> None:
    granted = grant(role_store.store, "lead-2", "lead")

    assert is_ok(granted), granted
    assert granted["value"]["granted_by"] == "human"
    assert seats(role_store.store)["lead-2"]["role"] == "lead"

    anonymous = invoke(
        "seat grant",
        {"seat": "lead-3", "role": "lead", "anchor": "project"},
        store=role_store.store,
    )
    assert refusal(anonymous) == ("SESSION_REQUIRED", "session_required")


def test_a_seat_with_can_grant_grants_a_subordinate(role_store: SimpleNamespace) -> None:
    granted = grant(
        role_store.store,
        "mgr-ops",
        "sprint-lead",
        anchor=role_store.orders,
        as_seat=role_store.lead,
    )

    assert is_ok(granted), granted
    assert granted["value"]["granted_by"] == role_store.lead
    assert seats(role_store.store)["mgr-ops"]["anchor"] == role_store.orders


def test_a_role_outside_the_granters_can_grant_is_refused(
    role_store: SimpleNamespace,
) -> None:
    granted = grant(
        role_store.store,
        "mgr-other",
        "sprint-lead",
        anchor=role_store.orders,
        as_seat=role_store.orders_seat,
    )

    assert refusal(granted) == ("PERMISSION_DENIED", "grant_denied")


def test_an_anchor_outside_the_granters_write_reach_is_refused(
    role_store: SimpleNamespace,
) -> None:
    granted = grant(
        role_store.store,
        "worker-1",
        "implementer",
        anchor=role_store.b1,
        as_seat=role_store.orders_seat,
    )

    assert refusal(granted) == ("PERMISSION_DENIED", "anchor_out_of_reach")


def test_an_anchor_of_the_wrong_kind_is_refused(role_store: SimpleNamespace) -> None:
    granted = grant(
        role_store.store,
        "mgr-wrong",
        "sprint-lead",
        anchor=role_store.o1,
        as_seat=role_store.lead,
    )

    assert refusal(granted) == ("VALIDATION_FAILED", "anchor_kind_mismatch")


def test_a_non_human_may_not_grant_a_human_role(role_store: SimpleNamespace) -> None:
    add_human_role(role_store.store)

    by_seat = grant(
        role_store.store, "human-2", "human-lead", as_seat=role_store.lead
    )
    by_human = grant(role_store.store, "human-2", "human-lead")

    assert refusal(by_seat) == ("PERMISSION_DENIED", "human_role_requires_human")
    assert is_ok(by_human), by_human


@pytest.mark.parametrize("seat", ["next", "find", "seat", "human"])
def test_a_reserved_seat_name_is_refused(
    role_store: SimpleNamespace, seat: str
) -> None:
    granted = grant(role_store.store, seat, "lead")

    assert refusal(granted) == ("VALIDATION_FAILED", "seat_name_reserved")


def test_a_duplicate_live_seat_name_is_refused(role_store: SimpleNamespace) -> None:
    granted = grant(role_store.store, role_store.orders_seat, "sprint-lead", anchor=role_store.orders)

    assert refusal(granted) == ("CONFLICT", "seat_name_taken")


def test_an_invalid_seat_name_is_refused(role_store: SimpleNamespace) -> None:
    granted = grant(role_store.store, "Mgr 1", "lead")

    assert refusal(granted) == ("VALIDATION_FAILED", "seat_name_invalid")


def test_a_profile_that_widens_its_role_is_refused(role_store: SimpleNamespace) -> None:
    granted = grant(
        role_store.store,
        "mgr-profile",
        "sprint-lead",
        anchor=role_store.orders,
        profile="implementer",
        as_seat=role_store.lead,
    )

    assert refusal(granted) == ("VALIDATION_FAILED", "profile_widens_role")


def test_auto_high_for_a_role_without_tick_is_refused(
    role_store: SimpleNamespace,
) -> None:
    granted = grant(
        role_store.store,
        "mgr-auto",
        "sprint-lead",
        anchor=role_store.orders,
        profile="cto",
        as_seat=role_store.lead,
    )

    assert refusal(granted) == ("VALIDATION_FAILED", "profile_auto_high")


def test_a_missing_profile_is_refused(role_store: SimpleNamespace) -> None:
    granted = grant(
        role_store.store,
        "mgr-missing",
        "sprint-lead",
        anchor=role_store.orders,
        profile="ghost",
        as_seat=role_store.lead,
    )

    assert granted["error"]["code"] == "VALIDATION_FAILED"
    assert "unknown profile" in granted["error"]["message"]


def test_a_role_skill_note_must_exist(role_store: SimpleNamespace) -> None:
    def change(roles: dict[str, Any]) -> None:
        roles["lead"]["skills"] = ["no-such-skill"]

    edit_roles(role_store.store, change)

    granted = grant(role_store.store, "lead-2", "lead")

    assert refusal(granted) == ("VALIDATION_FAILED", "role_skill_missing")


def test_a_grant_in_a_legacy_store_needs_roles(approved_store: Path) -> None:
    granted = invoke(
        "seat grant",
        {"seat": "lead-2", "role": "lead", "anchor": "project"},
        store=approved_store,
        actor="human",
    )

    assert refusal(granted) == ("INVALID_STATE", "no_roles")


def test_two_concurrent_grants_of_one_name_second_is_refused(
    role_store: SimpleNamespace,
) -> None:
    def attempt(_: int) -> dict[str, Any]:
        return grant(role_store.store, "mgr-race", "sprint-lead", anchor=role_store.orders)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))

    assert sorted(bool(result["ok"]) for result in results) == [False, True]
    lost = next(result for result in results if not result["ok"])
    assert (lost["error"]["code"], lost["error"]["details"]["reason"]) == (
        "CONFLICT",
        "seat_name_taken",
    )
