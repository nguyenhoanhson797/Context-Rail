"""The human boundary and the seat scope hold against wire-level callers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.store.sessions import open_session
from helpers import (
    as_human,
    daemon_descriptor,
    daemon_request,
    open_test_session,
    serving_daemon,
    write_record,
    write_run,
)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def grant_worker(store: Path, anchor: str) -> None:
    with as_human():
        granted = invoke(
            "seat grant",
            {"seat": "worker-1", "role": "implementer", "anchor": anchor},
            store=store,
            actor=None,
        )
    assert is_ok(granted), granted


def as_seat(store: Path, seat: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def test_anonymous_cannot_start_the_ui_human_channel(
    role_store: SimpleNamespace,
) -> None:
    with caller_identity(None, None, session=None):
        served = invoke("ui serve", {"port": 0}, store=role_store.store)

    assert refusal(served)[0] == "PERMISSION_DENIED"


def test_the_terminal_human_may_start_the_ui_channel(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(
        "context_rail.ui.server.serve",
        lambda store, *, actor, port: calls.append((store, actor, port)),
    )
    with as_human():
        served = invoke("ui serve", {"port": 0}, store=role_store.store, actor=None)

    assert is_ok(served), served
    assert calls and calls[0][1] == "human"


def test_a_daemon_request_cannot_claim_a_terminal(role_store: SimpleNamespace) -> None:
    with serving_daemon(role_store.store):
        descriptor = daemon_descriptor(role_store.store)
        assert descriptor is not None
        reply = daemon_request(
            descriptor["port"],
            {
                "tool": "seat revoke",
                "params": {"seat": role_store.billing_seat},
                "tty": True,
                "token": descriptor["token"],
            },
        )

    assert not reply["ok"], reply
    assert reply["error"]["code"] == "SESSION_REQUIRED"
    with as_human():
        listed = invoke("seat list", {}, store=role_store.store, actor=None)
    assert is_ok(listed), listed
    assert role_store.billing_seat in {row["seat"] for row in listed["value"]["seats"]}


def test_anonymous_reads_no_store_documents(role_store: SimpleNamespace) -> None:
    with caller_identity(None, None, session=None):
        shown = invoke("show", {"slug": role_store.b1}, store=role_store.store)

    assert refusal(shown) == ("PERMISSION_DENIED", "out_of_reach")


def test_a_lens_never_widens_the_callers_read_reach(
    role_store: SimpleNamespace,
) -> None:
    grant_worker(role_store.store, role_store.o1)
    token = open_test_session(role_store.store, "worker-1")
    with caller_identity(None, None, session=token, lens=role_store.orders_seat):
        shown = invoke("show", {"slug": role_store.o2}, store=role_store.store)

    assert refusal(shown) == ("PERMISSION_DENIED", "out_of_reach")


def test_revoke_closes_the_seat_session_and_a_regrant_does_not_revive_it(
    role_store: SimpleNamespace,
) -> None:
    grant_worker(role_store.store, role_store.o1)
    token = open_test_session(role_store.store, "worker-1")
    with caller_identity(None, None, session=token):
        first = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert is_ok(first), first

    with as_human():
        revoked = invoke(
            "seat revoke", {"seat": "worker-1"}, store=role_store.store, actor=None
        )
    assert is_ok(revoked), revoked
    assert revoked["value"]["closed"] == 1
    with caller_identity(None, None, session=token):
        after = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert refusal(after) == ("PERMISSION_DENIED", "session_invalid")

    grant_worker(role_store.store, role_store.o1)
    with caller_identity(None, None, session=token):
        regranted = invoke("show", {"slug": role_store.o1}, store=role_store.store)
    assert refusal(regranted) == ("PERMISSION_DENIED", "session_invalid")


def test_seat_close_all_also_closes_run_sessions(role_store: SimpleNamespace) -> None:
    token, _session = open_session(
        role_store.store,
        "worker@run-1",
        run="run-1",
        role="implementer",
        anchor=role_store.o1,
        profile=None,
        granted_by=role_store.lead,
        ttl_s=None,
    )
    with as_human():
        closed = invoke(
            "seat close", {"all": True}, store=role_store.store, actor=None
        )
    assert is_ok(closed), closed
    assert closed["value"]["closed"] >= 1
    from context_rail.store.sessions import resolve_session

    assert resolve_session(role_store.store, token) is None


def test_digest_hides_waits_and_progress_from_another_area(
    role_store: SimpleNamespace,
) -> None:
    for record_id, item in (
        ("01JWAITINSIDE0000000000001", role_store.o1),
        ("01JWAITOUTSIDE000000000000", role_store.b1),
    ):
        write_record(
            role_store.store,
            record_id,
            "report.filed",
            {
                "schema": 1,
                "target_class": "human-wait",
                "target": "copy",
                "item": item,
                "run": None,
                "nothing_to_report": False,
                "what_happened": "a human must decide this",
                "expected": "the default holds",
                "observed": "it does not",
                "impact": "medium",
                "evidence": [],
                "fingerprint": f"human-wait|copy|{item}",
            },
            about=(item,),
            at="2026-11-11T10:00:00Z",
        )

    digest = as_seat(role_store.store, role_store.orders_seat, "digest", {})

    assert is_ok(digest), digest
    items = {row.get("item") for row in digest["value"]["needs_human"]}
    assert role_store.o1 in items
    assert role_store.b1 not in items
    children = {
        child.get("slug")
        for child in (digest["value"].get("progress") or {}).get("children") or ()
    }
    assert role_store.billing not in children


def test_a_session_opened_before_its_grant_is_refused(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    _token, session = open_session(
        store,
        "worker-1",
        role="implementer",
        anchor="o1",
        profile="worker",
        granted_by="lead-1",
        now=100.0,
    )
    assert session.created == 100.0

    from context_rail.kernel.seats import LiveSeat

    seat = LiveSeat(
        seat="worker-1",
        role="implementer",
        anchor="o1",
        profile="worker",
        granted_by="lead-1",
        granted_at="2026-11-11T10:00:00Z",
    )
    from context_rail.gateway.identity import _opened_before_grant

    assert _opened_before_grant(session, seat.granted_at) is True


def link(store: Path, src: str, rel: str, dst: str) -> None:
    with as_human():
        shown = invoke("show", {"slug": src}, store=store, actor=None)
        linked = invoke(
            "link",
            {
                "src": src,
                "rel": rel,
                "dst": dst,
                "expect": shown["value"]["doc"]["etag"],
                "reason": "security fixture",
            },
            store=store,
            actor=None,
        )
    assert is_ok(linked), linked


def check_spec(tmp_path: Path) -> Path:
    import json

    path = tmp_path / "security-check.json"
    path.write_text(
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
    return path


def test_check_list_omits_checks_outside_reach(
    role_store: SimpleNamespace, tmp_path: Path
) -> None:
    spec = check_spec(tmp_path)
    with as_human():
        for slug, target in (("orders-check", role_store.o1), ("billing-check", role_store.b1)):
            created = invoke(
                "check",
                {
                    "slug": slug,
                    "target": target,
                    "spec_file": str(spec),
                    "expect": "absent",
                },
                store=role_store.store,
                actor=None,
            )
            assert is_ok(created), created

    listed = as_seat(role_store.store, role_store.orders_seat, "check list", {})

    assert is_ok(listed), listed
    slugs = {row["slug"] for row in listed["value"]["items"]}
    assert "orders-check" in slugs
    assert "billing-check" not in slugs


def test_status_hides_the_other_areas_queue_and_groups(
    role_store: SimpleNamespace,
) -> None:
    status = as_seat(role_store.store, role_store.orders_seat, "status", {})

    assert is_ok(status), status
    assert role_store.b1 not in {row["slug"] for row in status["value"]["queue"]["items"]}
    groups = status["value"]["groups"]
    assert all(
        role_store.b1 not in {row["slug"] for row in group["items"]}
        for group in groups
    )


def test_status_does_not_name_an_unreadable_dependency(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        added = invoke(
            "add",
            {
                "preset": "task",
                "title": "Needs the other area",
                "rel": [f"part_of={role_store.o1}"],
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(added), added
    task = added["value"]["slug"]
    link(role_store.store, task, "depends_on", role_store.b1)

    status = as_seat(role_store.store, role_store.orders_seat, "status", {})
    assert is_ok(status), status
    assert status["value"]["blocked"].get(task) == "depends_on"


def test_a_finished_dependency_outside_reach_does_not_keep_the_item_blocked(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        added = invoke(
            "add",
            {
                "preset": "task",
                "title": "Waits on a dropped risk",
                "rel": [f"part_of={role_store.o1}"],
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(added), added
        risk = invoke(
            "add",
            {"preset": "risk", "title": "Settled elsewhere"},
            store=role_store.store,
            actor=None,
        )
        assert is_ok(risk), risk
        moved = invoke(
            "move",
            {
                "slug": risk["value"]["slug"],
                "status": "dropped",
                "expect": invoke(
                    "show", {"slug": risk["value"]["slug"]}, store=role_store.store
                )["value"]["doc"]["etag"],
            },
            store=role_store.store,
            actor=None,
        )
        assert is_ok(moved), moved
    task = added["value"]["slug"]
    link(role_store.store, task, "depends_on", risk["value"]["slug"])

    status = as_seat(role_store.store, role_store.orders_seat, "status", {})
    assert is_ok(status), status
    assert task not in status["value"]["blocked"]


def test_ledger_totals_count_only_runs_inside_reach(
    role_store: SimpleNamespace,
) -> None:
    write_run(
        role_store.store,
        "01JREACHINSIDE0000000000001",
        item=role_store.o1,
        dispatch="inside",
        usage={"input_tokens": 7},
        at="2026-11-11T10:00:00Z",
    )
    write_run(
        role_store.store,
        "01JREACHOUTSIDE000000000000",
        item=role_store.b1,
        dispatch="outside",
        usage={"input_tokens": 11},
        at="2026-11-11T10:01:00Z",
    )

    ledger = as_seat(role_store.store, role_store.orders_seat, "ledger", {"limit": 20})

    assert is_ok(ledger), ledger
    assert ledger["value"]["totals"]["known_sum"]["input_tokens"] == 7


def test_show_hides_backlinks_outside_reach(role_store: SimpleNamespace) -> None:
    link(role_store.store, role_store.o1, "depends_on", role_store.b1)

    shown = as_seat(role_store.store, role_store.orders_seat, "show", {"slug": role_store.o1})

    assert is_ok(shown), shown
    for side in ("out", "in"):
        assert role_store.b1 not in {
            slug for slugs in (shown["value"].get(side) or {}).values() for slug in slugs
        }


def test_the_seat_brief_never_follows_a_relation_out_of_reach(
    role_store: SimpleNamespace,
) -> None:
    link(role_store.store, role_store.o1, "depends_on", role_store.b1)

    brief = as_seat(role_store.store, role_store.orders_seat, "cto brief", {})

    assert is_ok(brief), brief
    assert role_store.b1 not in brief["value"]["text"]
