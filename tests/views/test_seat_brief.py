"""Each seat's brief comes from its own sees recipe and its own documents."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def as_seat(store: Path, seat: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def implementer_seat(
    store: Path, parent: str, *, profile: str | None = None
) -> str:
    with as_human():
        added = invoke(
            "add",
            {"preset": "task", "title": "Implement me", "rel": [f"part_of={parent}"]},
            store=store,
            actor=None,
        )
    assert is_ok(added), added
    task = added["value"]["slug"]
    args: dict[str, Any] = {"seat": "impl-1", "role": "implementer", "anchor": task}
    if profile is not None:
        args["profile"] = profile
    with as_human():
        granted = invoke("seat grant", args, store=store, actor=None)
    assert is_ok(granted), granted
    return task


def test_brief_contains_only_declared_parts(role_store: SimpleNamespace) -> None:
    implementer_seat(role_store.store, role_store.o1)
    brief = as_seat(role_store.store, "impl-1", "cto brief", {})

    assert is_ok(brief), brief
    declared = {"title", "body", "acceptance", "code_area", "records"}
    assert set(brief["value"]["parts"]) <= declared
    assert "- diff" not in brief["value"]["text"]
    assert "- evidence" not in brief["value"]["text"]


def test_brief_scopes_documents_to_the_subtree_plus_declared_relations(
    role_store: SimpleNamespace,
) -> None:
    brief = as_seat(role_store.store, role_store.orders_seat, "cto brief", {})

    assert is_ok(brief), brief
    assert set(brief["value"]["documents"]) <= {
        role_store.orders,
        role_store.o1,
        role_store.o2,
    }
    assert role_store.b1 not in brief["value"]["text"]


def test_budget_is_the_minimum_of_role_and_profile(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        updated = invoke(
            "update",
            {
                "slug": "reviewer",
                "expect": _etag(role_store.store, "reviewer"),
                "set": [
                    "budget={max_chars: 120, max_docs: 1}",
                    "allowed_methods=[find, show, cto brief, ledger, report]",
                ],
                "confirm": "APPROVE reviewer",
            },
            store=role_store.store,
            actor=None,
        )
    assert is_ok(updated), updated
    implementer_seat(role_store.store, role_store.o1, profile="reviewer")

    brief = as_seat(role_store.store, "impl-1", "cto brief", {"max_chars": 100000})

    assert is_ok(brief), brief
    assert len(brief["value"]["text"]) <= 120
    assert len(brief["value"]["documents"]) == 1


def test_over_budget_trims_trailing_parts_and_reports_them(
    role_store: SimpleNamespace,
) -> None:
    brief = as_seat(
        role_store.store, role_store.orders_seat, "cto brief", {"max_chars": 150}
    )

    assert is_ok(brief), brief
    assert len(brief["value"]["text"]) <= 150
    assert brief["value"]["trimmed"] or brief["value"]["dropped"]
    assert "omitted:" in brief["value"]["text"]


def test_a_dropped_part_is_named_in_the_text(role_store: SimpleNamespace) -> None:
    brief = as_seat(
        role_store.store, role_store.orders_seat, "cto brief", {"max_chars": 100}
    )

    assert is_ok(brief), brief
    for part in brief["value"]["dropped"]:
        assert part in brief["value"]["text"]


def test_extra_parts_are_included_only_when_declared(
    role_store: SimpleNamespace,
) -> None:
    implementer_seat(role_store.store, role_store.o1)
    task = as_seat(role_store.store, "impl-1", "cto brief", {})
    assert is_ok(task)

    from context_rail.context.seat_brief import seat_brief
    from context_rail.gateway.gateway import Context
    from context_rail.gateway.identity import resolve_identity
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack

    manifest = load_manifest(role_store.store)
    context = Context(
        store=role_store.store,
        manifest=manifest,
        pack=load_pack(manifest.pack, role_store.store),
        actor="human",
    )
    context.identity = resolve_identity(
        context, actor=None, session=open_test_session(role_store.store, "impl-1")
    )
    with_extra = seat_brief(
        context,
        context.identity.seat,
        context.identity.role,
        extra_parts={"code_area": "src/app.py", "diff": "a big diff"},
    )
    assert "src/app.py" in with_extra["text"]
    assert "a big diff" not in with_extra["text"]


def test_seat_brief_for_a_role_and_anchor_without_a_live_seat(
    role_store: SimpleNamespace,
) -> None:
    from context_rail.context.seat_brief import seat_brief_for
    from context_rail.gateway.gateway import Context
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack

    task = implementer_seat(role_store.store, role_store.o1)
    manifest = load_manifest(role_store.store)
    context = Context(
        store=role_store.store,
        manifest=manifest,
        pack=load_pack(manifest.pack, role_store.store),
        actor="human",
    )

    brief = seat_brief_for(
        context,
        role_name="implementer",
        anchor=task,
        profile=None,
        granted_by=role_store.lead,
        extra_parts={"code_area": "src/app.py", "diff": "a big diff"},
    )

    assert brief["anchor"] == task
    assert brief["seat"] == "implementer"
    assert task in brief["documents"]
    assert "src/app.py" in brief["text"]
    assert "a big diff" not in brief["text"]
    with pytest.raises(LookupError):
        seat_brief_for(
            context,
            role_name="no-such-role",
            anchor=task,
            profile=None,
            granted_by=role_store.lead,
        )


@pytest.mark.parametrize("budget", [80, 200, 600, 2000, 12000])
def test_brief_never_exceeds_max_chars(
    role_store: SimpleNamespace, budget: int
) -> None:
    brief = as_seat(
        role_store.store, role_store.orders_seat, "cto brief", {"max_chars": budget}
    )

    assert is_ok(brief), brief
    assert len(brief["value"]["text"]) <= budget


def test_cto_brief_for_a_restricted_seat_is_the_seat_brief(
    role_store: SimpleNamespace,
) -> None:
    brief = as_seat(role_store.store, role_store.orders_seat, "cto brief", {})

    assert is_ok(brief), brief
    assert brief["value"]["seat"] == role_store.orders_seat
    assert brief["value"]["anchor"] == role_store.orders
    assert "state" not in brief["value"]


def test_legacy_brief_is_unchanged(approved_store: Path) -> None:
    brief = invoke("cto brief", {}, store=approved_store, actor="cto")

    assert is_ok(brief), brief
    assert "state" in brief["value"]
    assert "seat" not in brief["value"]


def test_a_seat_brief_names_its_role(role_store: SimpleNamespace) -> None:
    brief = as_seat(role_store.store, role_store.orders_seat, "cto brief", {})

    assert is_ok(brief), brief
    text = brief["value"]["text"]
    header = f"# Seat {role_store.orders_seat} on {role_store.orders}\n\n"
    assert text.startswith(header + "You are acting as: sprint-lead")

    tight = as_seat(
        role_store.store, role_store.orders_seat, "cto brief", {"max_chars": 80}
    )
    assert is_ok(tight), tight
    assert len(tight["value"]["text"]) <= 80
    assert "You are acting as: sprint-lead" in tight["value"]["text"]


def _etag(store: Path, slug: str) -> str:
    with as_human():
        shown = invoke("show", {"slug": slug}, store=store, actor=None)
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]
