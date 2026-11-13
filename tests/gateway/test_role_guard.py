"""Role-mode authorization: a seat writes only through its role and its reach."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import actors_for, as_human, open_test_session, write_record


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


def test_manager_updates_its_own_story(role_store: SimpleNamespace) -> None:
    updated = as_seat(
        role_store.store,
        role_store.orders_seat,
        "update",
        {
            "slug": role_store.o1,
            "expect": etag_of(role_store.store, role_store.orders_seat, role_store.o1),
            "tag": ["+revised"],
        },
    )

    assert is_ok(updated), updated


def test_manager_cannot_update_a_sibling_sprints_story(
    role_store: SimpleNamespace,
) -> None:
    updated = as_seat(
        role_store.store,
        role_store.orders_seat,
        "update",
        {
            "slug": role_store.b1,
            "expect": etag_of(role_store.store, role_store.orders_seat, role_store.b1),
            "tag": ["+revised"],
        },
    )

    assert refusal(updated) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_manager_cannot_add_without_a_parent_in_reach(
    role_store: SimpleNamespace,
) -> None:
    added = as_seat(
        role_store.store,
        role_store.orders_seat,
        "add",
        {"preset": "story", "title": "Orphan"},
    )

    assert refusal(added) == ("PERMISSION_DENIED", "add_without_parent")


def test_manager_adding_with_two_parents_needs_both_in_reach(
    role_store: SimpleNamespace,
) -> None:
    added = as_seat(
        role_store.store,
        role_store.orders_seat,
        "add",
        {
            "preset": "story",
            "title": "Two homes",
            "rel": [f"part_of={role_store.orders}", f"part_of={role_store.billing}"],
        },
    )

    assert refusal(added) == ("PERMISSION_DENIED", "add_outside_reach")


def test_check_cannot_overwrite_a_check_document_outside_reach(
    role_store: SimpleNamespace, tmp_path: Path
) -> None:
    import json

    spec = tmp_path / "check.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "cmd",
                "acceptance": False,
                "cmd": ["py", "-c", "import sys; sys.exit(0)"],
                "inputs": ["src/*.py"],
                "timeout_s": 30,
            }
        ),
        encoding="utf-8",
    )
    with as_human():
        created = invoke(
            "check",
            {
                "slug": "foreign-check",
                "target": role_store.o1,
                "spec_file": str(spec),
                "expect": "absent",
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(created), created
        shown = invoke(
            "show", {"slug": "foreign-check"}, store=role_store.store, actor=None
        )
    assert is_ok(shown), shown

    overwritten = as_seat(
        role_store.store,
        role_store.orders_seat,
        "check",
        {
            "slug": "foreign-check",
            "target": role_store.o1,
            "spec_file": str(spec),
            "expect": shown["value"]["doc"]["etag"],
        },
    )

    assert refusal(overwritten) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_manager_adds_a_story_under_its_sprint(role_store: SimpleNamespace) -> None:
    added = as_seat(
        role_store.store,
        role_store.orders_seat,
        "add",
        {"preset": "story", "title": "O3", "rel": [f"part_of={role_store.orders}"]},
    )

    assert is_ok(added), added
    assert role_store.orders_seat in actors_for(
        role_store.store, added["value"]["slug"]
    )


def test_move_to_a_status_outside_can_move_is_refused(
    role_store: SimpleNamespace,
) -> None:
    def narrow(roles: dict[str, Any]) -> None:
        roles["sprint-lead"]["can"]["move"]["story"] = ["todo"]

    edit_roles(role_store.store, narrow)
    moved = as_seat(
        role_store.store,
        role_store.orders_seat,
        "move",
        {
            "slug": role_store.o1,
            "status": "doing",
            "expect": etag_of(role_store.store, role_store.orders_seat, role_store.o1),
        },
    )

    assert refusal(moved) == ("PERMISSION_DENIED", "status_not_in_can_move")


def test_a_write_tool_missing_from_the_table_is_refused(
    role_store: SimpleNamespace,
) -> None:
    from context_rail.gateway.gateway import Context
    from context_rail.gateway.identity import resolve_identity
    from context_rail.gateway.role_guard import authorize_role
    from context_rail.gateway.tools import Tool
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
        context,
        actor=None,
        session=open_test_session(role_store.store, role_store.orders_seat),
    )
    future = Tool(
        name="future write",
        summary="a write with no rule yet",
        permission="write",
        params=(),
        handler="test:unused",
    )

    refused = authorize_role(future, {}, context)

    assert refused is not None
    assert refused["error"]["details"]["reason"] == "tool_not_for_seats"


def test_ops_tools_are_human_only_for_seats(role_store: SimpleNamespace) -> None:
    synced = as_seat(role_store.store, role_store.orders_seat, "sync", {})

    assert refusal(synced) == ("PERMISSION_DENIED", "ops_human_only")


def test_anonymous_write_is_session_required(role_store: SimpleNamespace) -> None:
    added = invoke(
        "add",
        {"preset": "story", "title": "Anonymous", "rel": [f"part_of={role_store.orders}"]},
        store=role_store.store,
    )

    assert refusal(added) == ("SESSION_REQUIRED", "session_required")


def test_lens_write_is_refused(role_store: SimpleNamespace) -> None:
    with caller_identity(None, None, tty=True, lens=role_store.billing_seat):
        added = invoke(
            "add",
            {
                "preset": "story",
                "title": "Through the lens",
                "rel": [f"part_of={role_store.billing}"],
            },
            store=role_store.store,
        )

    assert refusal(added) == ("PERMISSION_DENIED", "lens_read_only")


def test_human_terminal_passes_the_guard(role_store: SimpleNamespace) -> None:
    with as_human():
        added = invoke(
            "add",
            {"preset": "story", "title": "By the human", "rel": [f"part_of={role_store.billing}"]},
            store=role_store.store,
            actor=None,
        )

    assert is_ok(added), added


def test_lead_with_project_reach_writes_anywhere(role_store: SimpleNamespace) -> None:
    added = as_seat(
        role_store.store,
        role_store.lead,
        "add",
        {"preset": "story", "title": "Lead anywhere", "rel": [f"part_of={role_store.billing}"]},
    )

    assert is_ok(added), added


def test_seat_role_removed_is_inert(role_store: SimpleNamespace) -> None:
    def remove(roles: dict[str, Any]) -> None:
        del roles["sprint-lead"]
        for role in roles.values():
            grants = role.get("can", {}).get("grant")
            if isinstance(grants, list) and "sprint-lead" in grants:
                grants.remove("sprint-lead")

    edit_roles(role_store.store, remove)
    added = as_seat(
        role_store.store,
        role_store.orders_seat,
        "add",
        {"preset": "story", "title": "Stale seat", "rel": [f"part_of={role_store.orders}"]},
    )

    assert refusal(added) == ("PERMISSION_DENIED", "seat_role_unknown")


def test_legacy_store_still_uses_allowed_methods(approved_store: Path) -> None:
    refused = invoke(
        "add", {"preset": "task", "title": "Not allowed"}, store=approved_store, actor="implementer"
    )
    allowed = invoke(
        "add", {"preset": "task", "title": "Allowed"}, store=approved_store, actor="cto"
    )

    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["profile"] == "implementer"
    assert is_ok(allowed), allowed


def test_anonymous_cannot_repair_a_role_store(role_store: SimpleNamespace) -> None:
    repaired = invoke("ops repair", {}, store=role_store.store)

    assert refusal(repaired) == ("SESSION_REQUIRED", "session_required")


@pytest.mark.parametrize(
    "tool,args",
    [
        ("why", {"slug": "b1"}),
        ("next", {"item": "b1"}),
    ],
)
def test_a_manager_cannot_read_a_sibling_sprints_story(
    role_store: SimpleNamespace, tool: str, args: dict[str, Any]
) -> None:
    read = as_seat(role_store.store, role_store.orders_seat, tool, args)

    assert refusal(read) == ("PERMISSION_DENIED", "out_of_reach")


def test_the_model_omits_documents_outside_reach(role_store: SimpleNamespace) -> None:
    model = as_seat(role_store.store, role_store.orders_seat, "model", {})

    assert is_ok(model), model
    group = model["value"]["hierarchy"][0]
    shown = {item["slug"] for item in group["items"]}
    assert group["omitted_outside_reach"] == 1
    assert role_store.billing not in shown
    assert role_store.orders in shown


def test_the_lead_brief_omits_documents_outside_reach(
    role_store: SimpleNamespace,
) -> None:
    brief = as_seat(role_store.store, role_store.lead, "cto brief", {})

    assert is_ok(brief), brief
    assert brief["value"]["omitted_outside_reach"] == 0
    scoped = as_seat(role_store.store, role_store.orders_seat, "cto brief", {})
    assert is_ok(scoped), scoped
    assert role_store.b1 not in scoped["value"]["text"]


def test_a_seat_cannot_triage_a_report_about_a_sibling_sprint(
    role_store: SimpleNamespace, report_payload: Any
) -> None:
    report = "01JREPORT0000000000000201"
    write_record(
        role_store.store,
        report,
        "report.filed",
        report_payload(
            target=role_store.b1,
            item=role_store.b1,
            fingerprint=f"item|{role_store.b1}|expected|observed",
        ),
        at="2026-11-10T12:00:00Z",
    )

    refused = as_seat(
        role_store.store, role_store.orders_seat, "triage", {"report": report}
    )

    assert refusal(refused) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_a_rename_cannot_rewrite_a_document_outside_the_seats_reach(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        etag = invoke("show", {"slug": role_store.b1}, store=role_store.store, actor=None)[
            "value"
        ]["doc"]["etag"]
        linked = invoke(
            "link",
            {
                "src": role_store.b1,
                "rel": "depends_on",
                "dst": role_store.o1,
                "expect": etag,
                "reason": "planning",
            },
            store=role_store.store,
            actor=None,
        )
    assert is_ok(linked), linked

    refused = as_seat(
        role_store.store,
        role_store.orders_seat,
        "rename",
        {"old": role_store.o1, "new": "o1-renamed"},
    )

    assert refusal(refused) == ("PERMISSION_DENIED", "write_out_of_reach")
