"""``rail loop brief`` in a store with roles: a seat reads only the briefs it may grant."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from context_rail.context.work_brief import WORK_BRIEF_SECTIONS
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.seats import SEAT_GRANTED
from helpers import as_human, as_seat, refusal, run_git, start_repo, write_record


def grant_reviewer(seated: SimpleNamespace, lead: str) -> None:
    write_record(
        seated.store,
        "seat-rev-1",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "rev-1",
            "role": "reviewer",
            "anchor": seated.task,
            "granted_by": lead,
        },
        at="2026-11-10T10:04:00Z",
        actor=lead,
    )


def commit_candidate(seated: SimpleNamespace) -> str:
    start_repo(seated)
    path = Path(seated.root) / "src" / "app.py"
    path.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(seated.root, "add", "src/app.py")
    run_git(seated.root, "commit", "-m", "change")
    recorded = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )
    assert is_ok(recorded), recorded
    return recorded["value"]["record"]


def test_a_seat_reads_the_brief_of_its_own_role(seated: SimpleNamespace) -> None:
    result = as_seat(seated.store, seated.seat, "brief", {"item": seated.task})

    assert is_ok(result), result
    value = result["value"]
    assert value["role"] == "implementer"
    assert tuple(value["sections"]) == WORK_BRIEF_SECTIONS
    assert "rail loop candidate" in value["sections"]["recording"]


def test_the_lead_reads_a_role_it_can_grant(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    result = as_seat(
        seated.store, role_store.lead, "brief", {"item": seated.task, "role": "implementer"}
    )

    assert is_ok(result), result
    assert result["value"]["role"] == "implementer"


def test_the_human_reads_any_declared_role(seated: SimpleNamespace) -> None:
    with as_human():
        result = invoke(
            "brief",
            {"item": seated.task, "role": "implementer"},
            store=seated.store,
            actor=None,
        )

    assert is_ok(result), result


def test_a_human_must_name_the_role(seated: SimpleNamespace) -> None:
    with as_human():
        result = invoke("brief", {"item": seated.task}, store=seated.store, actor=None)

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert "implementer" in result["error"]["details"]["candidates"]


def test_a_reviewer_seat_cannot_read_the_implementer_brief(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    grant_reviewer(seated, role_store.lead)

    result = as_seat(seated.store, "rev-1", "brief", {"item": seated.task, "role": "implementer"})

    assert refusal(result) == ("PERMISSION_DENIED", "brief_role_not_grantable")


def test_a_reviewer_seat_reads_the_diff_of_the_candidate(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    record = commit_candidate(seated)
    grant_reviewer(seated, role_store.lead)

    result = as_seat(seated.store, "rev-1", "brief", {"item": seated.task})

    assert is_ok(result), result
    value = result["value"]
    assert value["role"] == "reviewer"
    assert value["candidate"] == record
    assert "VALUE = 2" in value["sections"]["diff"]


def test_a_reviewer_seat_without_a_candidate_is_refused(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    start_repo(seated)
    grant_reviewer(seated, role_store.lead)

    result = as_seat(seated.store, "rev-1", "brief", {"item": seated.task})

    assert refusal(result) == ("INVALID_STATE", "no_candidate")


def test_an_item_outside_the_seats_reach_is_refused(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    result = as_seat(seated.store, seated.seat, "brief", {"item": role_store.b1})

    assert refusal(result) == ("PERMISSION_DENIED", "out_of_reach")