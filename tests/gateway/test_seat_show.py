"""``seat show``: one seat's role, reach, skills, budget and queue head."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def as_seat(store: Path, seat: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke("seat show", args or {}, store=store)


def as_lens(store: Path, by: str, seat: str) -> dict[str, Any]:
    token = open_test_session(store, by)
    with caller_identity(None, None, session=token, lens=seat):
        return invoke("seat show", {}, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def test_seat_card_lists_reach_can_skills_and_queue(
    role_store: SimpleNamespace,
) -> None:
    card = as_seat(role_store.store, role_store.orders_seat)

    assert is_ok(card), card
    value = card["value"]
    assert value["seat"] == role_store.orders_seat
    assert value["role"] == "sprint-lead"
    assert value["anchor"]["slug"] == role_store.orders
    assert value["reach"]["documents"] > 0
    assert "read_presets" in value["reach"]
    assert "update" in value["can"] and "story" in value["can"]["update"]
    assert value["skills"] == []
    assert value["budget"] == {"chars": 8000, "docs": 40}
    assert value["workspace"] == "none"
    assert value["session"] and value["session"]["expires"]
    assert value["queue_head"]
    assert set(value["queue_head"]) <= {role_store.o1, role_store.o2}


def test_seat_show_defaults_to_the_callers_own_seat(
    role_store: SimpleNamespace,
) -> None:
    card = as_seat(role_store.store, role_store.lead)

    assert is_ok(card), card
    assert card["value"]["seat"] == role_store.lead


def test_card_for_an_unknown_seat_lists_live_candidates(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        card = invoke("seat show", {"seat": "mgr-ghost"}, store=role_store.store, actor=None)

    assert refusal(card) == ("NOT_FOUND", "seat_unknown")
    assert set(card["error"]["details"]["candidates"]) == {
        role_store.lead,
        role_store.orders_seat,
        role_store.billing_seat,
    }


def test_card_of_a_seat_outside_the_callers_reach_is_refused(
    role_store: SimpleNamespace,
) -> None:
    card = as_seat(role_store.store, role_store.orders_seat, {"seat": role_store.billing_seat})

    assert refusal(card) == ("PERMISSION_DENIED", "out_of_reach")


def test_terminal_can_view_any_seat_read_only(role_store: SimpleNamespace) -> None:
    with as_human():
        card = invoke(
            "seat show", {"seat": role_store.billing_seat}, store=role_store.store, actor=None
        )

    assert is_ok(card), card
    assert card["value"]["seat"] == role_store.billing_seat
    assert card["value"]["session"] is None


def test_a_missing_skill_note_is_a_problem(role_store: SimpleNamespace) -> None:
    path = role_store.store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["operating"]["roles"]["lead"]["skills"] = ["no-such-skill"]
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )

    card = as_seat(role_store.store, role_store.lead)

    assert is_ok(card), card
    assert any("no-such-skill" in problem for problem in card["value"]["problems"])


def test_an_anonymous_caller_reaches_no_seat_card(role_store: SimpleNamespace) -> None:
    card = invoke(
        "seat show", {"seat": role_store.orders_seat}, store=role_store.store
    )

    assert refusal(card) == ("PERMISSION_DENIED", "out_of_reach")


def test_seat_show_queue_head_intersects_with_caller_reach(
    role_store: SimpleNamespace,
) -> None:
    """When viewing another seat whose anchor is readable, queue_head stays inside caller reach."""
    path = role_store.store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["operating"]["roles"]["narrow-viewer"] = {
        "anchor": {"item": ["story"]},
        "reach": {"read": ["anchor", {"up": 1}], "write": []},
        "can": {},
        "sees": {"up": 1, "parts": ["title"], "budget": {"chars": 8000, "docs": 10}},
        "workspace": "none",
    }
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "viewer-o1", "role": "narrow-viewer", "anchor": role_store.o1},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(granted), granted

    card = as_seat(
        role_store.store, "viewer-o1", {"seat": role_store.orders_seat}
    )
    assert is_ok(card), card
    assert role_store.o2 not in card["value"].get("queue_head", [])

