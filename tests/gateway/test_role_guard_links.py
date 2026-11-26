"""Role-mode authorization for links, lifecycle calls and declared actions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def edit_roles(store: Path, change: Any) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document["operating"]["roles"])
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )


def as_seat(
    store: Path, seat: str, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def etag_of(store: Path, seat: str, slug: str) -> str:
    with as_human():
        shown = invoke("show", {"slug": slug}, store=store, actor=None)
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def link(
    store: Path,
    seat: str,
    src: str,
    rel: str,
    dst: str,
    *,
    remove: bool = False,
) -> dict[str, Any]:
    return as_seat(
        store,
        seat,
        "link",
        {
            "src": src,
            "rel": rel,
            "dst": dst,
            "expect": etag_of(store, seat, src),
            "remove": remove,
            "reason": "role guard test",
        },
    )


def test_link_into_another_sprint_needs_write_reach_on_both_parents(
    role_store: SimpleNamespace,
) -> None:
    moved = link(
        role_store.store,
        role_store.orders_seat,
        role_store.o1,
        "part_of",
        role_store.billing,
    )

    assert refusal(moved) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_relink_a_story_to_the_other_sprint_is_refused(
    role_store: SimpleNamespace,
) -> None:
    moved = link(
        role_store.store,
        role_store.orders_seat,
        role_store.b1,
        "part_of",
        role_store.orders,
    )

    assert refusal(moved) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_link_to_a_readable_cited_spec_is_allowed(role_store: SimpleNamespace) -> None:
    def widen(roles: dict[str, Any]) -> None:
        roles["sprint-lead"]["can"]["link"].append("about")

    edit_roles(role_store.store, widen)
    cited = link(
        role_store.store,
        role_store.orders_seat,
        role_store.o1,
        "about",
        role_store.rules,
    )

    assert is_ok(cited), cited


def test_link_relation_outside_can_link_is_refused(
    role_store: SimpleNamespace,
) -> None:
    linked = link(
        role_store.store,
        role_store.orders_seat,
        role_store.o1,
        "about",
        role_store.o2,
    )

    assert refusal(linked) == ("PERMISSION_DENIED", "relation_not_in_can_link")


def test_verify_of_an_item_outside_reach_is_refused(
    role_store: SimpleNamespace,
) -> None:
    verified = as_seat(
        role_store.store,
        role_store.orders_seat,
        "verify",
        {"target": role_store.b1},
    )

    assert refusal(verified) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_do_action_outside_can_actions_is_refused(
    role_store: SimpleNamespace,
) -> None:
    acted = as_seat(
        role_store.store,
        role_store.orders_seat,
        "do",
        {"action": "start", "item": role_store.o1, "key": "guard-do"},
    )

    assert refusal(acted) == ("PERMISSION_DENIED", "action_not_in_can_actions")



def test_replace_needs_write_reach_on_every_previous_parent(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        shared = invoke(
            "link",
            {
                "src": role_store.o1,
                "rel": "part_of",
                "dst": role_store.billing,
                "expect": etag_of(role_store.store, role_store.orders_seat, role_store.o1),
            },
            store=role_store.store,
            actor=None,
        )
    assert is_ok(shared), shared
    token = open_test_session(role_store.store, role_store.orders_seat)
    with caller_identity(None, None, session=token):
        moved = invoke(
            "link",
            {
                "src": role_store.o1,
                "rel": "part_of",
                "dst": role_store.orders,
                "expect": etag_of(role_store.store, role_store.orders_seat, role_store.o1),
                "replace": True,
                "reason": "role guard test",
            },
            store=role_store.store,
        )

    assert refusal(moved) == ("PERMISSION_DENIED", "write_out_of_reach")
