"""Scoped reads: every aggregate and direct read respects the seat's reach."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session, write_candidate


def as_seat(store: Path, seat: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def slugs(result: dict[str, Any]) -> set[str]:
    return {row["slug"] for row in result["value"]["items"]}


def test_find_returns_only_documents_in_reach_and_counts_the_rest(
    role_store: SimpleNamespace,
) -> None:
    found = as_seat(role_store.store, role_store.orders_seat, "find", {"limit": 50})

    assert is_ok(found), found
    assert found["value"]["omitted_outside_reach"] > 0
    assert slugs(found) <= {
        role_store.orders,
        role_store.o1,
        role_store.o2,
        role_store.rules,
    }
    assert role_store.b1 not in slugs(found)


def test_show_of_a_sibling_story_is_refused(role_store: SimpleNamespace) -> None:
    shown = as_seat(role_store.store, role_store.orders_seat, "show", {"slug": role_store.b1})

    assert refusal(shown) == ("PERMISSION_DENIED", "out_of_reach")


def test_show_of_a_story_in_reach_works(role_store: SimpleNamespace) -> None:
    shown = as_seat(role_store.store, role_store.orders_seat, "show", {"slug": role_store.o1})

    assert is_ok(shown), shown
    assert shown["value"]["doc"]["slug"] == role_store.o1


def test_status_counts_only_the_seats_subtree(role_store: SimpleNamespace) -> None:
    status = as_seat(role_store.store, role_store.orders_seat, "status", {})

    assert is_ok(status), status
    counts = status["value"]["counts"]
    assert sum(counts.values()) == 3
    assert status["value"]["omitted_outside_reach"] >= 3


def test_ledger_filters_to_reachable_items(role_store: SimpleNamespace) -> None:
    write_candidate(role_store.store, "cand-reach-o1", role_store.o1, "1" * 40)
    write_candidate(role_store.store, "cand-reach-b1", role_store.b1, "2" * 40)

    ledger = as_seat(role_store.store, role_store.orders_seat, "ledger", {"limit": 20})

    assert is_ok(ledger), ledger
    assert {row["item"] for row in ledger["value"]["candidates"]} == {role_store.o1}
    assert ledger["value"]["omitted_outside_reach"] == 1


def test_why_out_of_reach_is_refused(role_store: SimpleNamespace) -> None:
    why = as_seat(role_store.store, role_store.orders_seat, "why", {"slug": role_store.b1})

    assert refusal(why) == ("PERMISSION_DENIED", "out_of_reach")


def test_digest_lists_only_reachable_reports(
    role_store: SimpleNamespace, tmp_path: Path, report_payload: Any
) -> None:
    for item, run_id in ((role_store.o1, "run-report-o1"), (role_store.b1, "run-report-b1")):
        path = tmp_path / f"{run_id}.json"
        path.write_text(json.dumps(report_payload(item=item)), encoding="utf-8")
        with as_human():
            filed = invoke(
                "report",
                {"item": item, "report_file": str(path)},
                store=role_store.store,
                actor=None,
            )
        assert is_ok(filed), filed

    digest = as_seat(role_store.store, role_store.orders_seat, "digest", {"limit": 20})

    assert is_ok(digest), digest
    reported = {row.get("item") for row in digest["value"]["reports"]["recent"]}
    assert role_store.b1 not in reported
    assert digest["value"]["omitted_outside_reach"] >= 1


def test_a_lead_with_project_reach_sees_everything_unchanged(
    role_store: SimpleNamespace,
) -> None:
    found = as_seat(role_store.store, role_store.lead, "find", {"limit": 50})

    assert is_ok(found), found
    assert found["value"]["omitted_outside_reach"] == 0
    assert role_store.b1 in slugs(found)


def test_the_terminal_human_sees_everything(role_store: SimpleNamespace) -> None:
    with as_human():
        found = invoke("find", {"limit": 50}, store=role_store.store, actor=None)

    assert is_ok(found), found
    assert "omitted_outside_reach" not in found["value"]
    assert role_store.b1 in slugs(found)


def test_legacy_reads_are_unchanged(approved_store: Path) -> None:
    found = invoke("find", {"limit": 50}, store=approved_store, actor="cto")
    status = invoke("status", {}, store=approved_store, actor="cto")

    assert is_ok(found) and is_ok(status)
    assert "omitted_outside_reach" not in found["value"]
    assert "omitted_outside_reach" not in status["value"]


def test_a_read_does_not_write_the_store(role_store: SimpleNamespace) -> None:
    def store_files() -> dict[str, bytes]:
        root = role_store.store
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".rail-local" not in path.parts
        }

    before = store_files()
    as_seat(role_store.store, role_store.orders_seat, "find", {"limit": 50})
    as_seat(role_store.store, role_store.orders_seat, "status", {})
    as_seat(role_store.store, role_store.orders_seat, "show", {"slug": role_store.o1})

    assert store_files() == before
