"""``next`` for a seat: its own anchor's card or queue, never the whole project."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def as_seat(store: Path, seat: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke("next", args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def add_task(store: Path, title: str, parent: str) -> str:
    with as_human():
        added = invoke(
            "add",
            {"preset": "task", "title": title, "rel": [f"part_of={parent}"]},
            store=store,
            actor=None,
        )
    assert is_ok(added), added
    return added["value"]["slug"]


def test_sprint_lead_next_is_its_sprint_queue(role_store: SimpleNamespace) -> None:
    queued = as_seat(role_store.store, role_store.orders_seat, {})

    assert is_ok(queued), queued
    assert set(queued["value"]["queue"]) == {role_store.o1, role_store.o2}
    assert role_store.b1 not in queued["value"]["queue"]


def test_lead_next_is_the_project_queue(role_store: SimpleNamespace) -> None:
    queued = as_seat(role_store.store, role_store.lead, {})

    assert is_ok(queued), queued
    assert {role_store.o1, role_store.o2, role_store.b1, role_store.b2} <= set(
        queued["value"]["queue"]
    )


def test_implementer_next_defaults_to_its_task_card(
    role_store: SimpleNamespace,
) -> None:
    task = add_task(role_store.store, "Implement me", role_store.o1)
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "impl-1", "role": "implementer", "anchor": task},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(granted), granted

    card = as_seat(role_store.store, "impl-1", {})

    assert is_ok(card), card
    assert card["value"]["slug"] == task
    assert "queue" not in card["value"]


def test_next_into_another_sprint_is_refused(role_store: SimpleNamespace) -> None:
    queued = as_seat(
        role_store.store, role_store.orders_seat, {"into": role_store.billing}
    )

    assert refusal(queued) == ("PERMISSION_DENIED", "out_of_reach")


def test_queue_lists_are_capped_with_omitted_counts(
    role_store: SimpleNamespace,
) -> None:
    for index in range(55):
        add_task(role_store.store, f"Story {index}", role_store.orders)

    queued = as_seat(role_store.store, role_store.orders_seat, {})

    assert is_ok(queued), queued
    assert len(queued["value"]["queue"]) == 50
    assert len(queued["value"]["items"]) == 50
    assert queued["value"]["omitted"]["queue"] >= 5
    assert queued["value"]["omitted"]["items"] >= 5


def test_legacy_next_is_unchanged(approved_store: Path) -> None:
    queued = invoke("next", {}, store=approved_store, actor="cto")

    assert is_ok(queued), queued
    assert "queue" in queued["value"] and "items" in queued["value"]
    assert "omitted" in queued["value"]
