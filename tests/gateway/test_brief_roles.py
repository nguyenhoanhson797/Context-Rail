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
    assert tuple(value["sections"]) == (*WORK_BRIEF_SECTIONS, "result")
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


def test_a_reviewer_seat_without_a_candidate_or_result_is_refused(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    start_repo(seated)
    grant_reviewer(seated, role_store.lead)

    result = as_seat(seated.store, "rev-1", "brief", {"item": seated.task})

    assert refusal(result) == ("INVALID_STATE", "no_result_or_candidate")


def test_a_reviewer_seat_without_a_candidate_is_refused_in_contractless_pack(
    tmp_path: Path,
) -> None:
    from helpers import _write_charter, approve_picture, init_pack_store, nested_pack_document

    doc = nested_pack_document("legacy-roles")
    doc["version"] = 2
    doc["presets"]["task"]["workflow"] = "sprint"
    doc["workflows"] = {
        "sprint": {
            "initial": "todo",
            "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
        }
    }
    doc["statuses"] = {"todo": "not_started", "doing": "active", "done": "done"}
    doc["operating"] = {
        "version": 2,
        "unit": "task",
        "hierarchy": [{"preset": "phase", "required": True}],
        "roles": {
            "lead": {
                "anchor": "project",
                "reach": {"read": ["project"], "write": ["project"], "code": {"read": ["**"], "write": []}},
                "can": {"add": ["phase", "task"], "grant": ["implementer", "reviewer"]},
                "sees": {"up": 0, "down": 1, "parts": ["title", "status"]},
            },
            "implementer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": ["**"]}},
                "can": {"candidate": ["task"]},
                "sees": {"up": 1, "parts": ["title", "body"]},
            },
            "reviewer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": []}},
                "independent_of": ["produce"],
                "can": {"review": ["task"]},
                "sees": {"up": 1, "parts": ["title", "diff", "evidence"]},
            },
        },
    }
    project = tmp_path / "proj"
    store = init_pack_store(project, doc, approve=False)
    _write_charter(store)
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        assert is_ok(approve_picture(store, actor=None))
        phase = invoke("add", {"preset": "phase", "title": "P1"}, store=store, actor=None)["value"]["slug"]
        task = invoke("add", {"preset": "task", "title": "T1", "rel": [f"part_of={phase}"]}, store=store, actor=None)["value"]["slug"]
        assert is_ok(invoke("seat grant", {"seat": "rev-1", "role": "reviewer", "anchor": task}, store=store, actor=None))

    result = as_seat(store, "rev-1", "brief", {"item": task})

    assert refusal(result) == ("INVALID_STATE", "no_candidate")


def test_a_reviewer_seat_with_commitless_result_reads_result_brief_without_git(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess
    from context_rail.runner import git as runner_git
    from helpers import DEFAULT_PRODUCER_SECTIONS, DEFAULT_REVIEW_SECTIONS, init_pack_store, result_body, result_pack_document

    doc = result_pack_document()
    doc["presets"]["phase"] = {"primitive": "item", "tags": ["phase"], "container": True}
    doc["operating"] = {
        "version": 2,
        "unit": "task",
        "hierarchy": [{"preset": "phase", "required": True}],
        "roles": {
            "lead": {
                "anchor": "project",
                "reach": {"read": ["project"], "write": ["project"], "code": {"read": ["**"], "write": []}},
                "can": {"add": ["phase", "task"], "grant": ["implementer", "reviewer"]},
                "sees": {"up": 0, "down": 1, "parts": ["title", "status", "results"]},
            },
            "implementer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": ["**"]}},
                "can": {"result": ["task"], "candidate": ["task"]},
                "sees": {"up": 1, "parts": ["title", "body", "results"]},
            },
            "reviewer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": []}},
                "independent_of": ["produce"],
                "can": {"review": ["task"]},
                "sees": {"up": 1, "parts": ["title", "diff", "results", "evidence"]},
            },
        },
    }
    project = tmp_path / "proj"
    templates = project / "context-rail" / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    store = init_pack_store(
        project,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    from helpers import _write_charter, approve_picture

    _write_charter(store)
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        assert is_ok(approve_picture(store, actor=None))
        phase = invoke("add", {"preset": "phase", "title": "P1"}, store=store, actor=None)["value"]["slug"]
        task = invoke("add", {"preset": "task", "title": "T1", "rel": [f"part_of={phase}"]}, store=store, actor=None)["value"]["slug"]
        assert is_ok(invoke("seat grant", {"seat": "impl-1", "role": "implementer", "anchor": task}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "rev-1", "role": "reviewer", "anchor": task}, store=store, actor=None))

    body_path = tmp_path / "res.md"
    body_path.write_text(result_body(DEFAULT_PRODUCER_SECTIONS), encoding="utf-8", newline="\n")
    produced = as_seat(store, "impl-1", "result", {"item": task, "body_file": str(body_path)})
    assert is_ok(produced), produced

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("commitless reviewer brief must not run git")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(runner_git, "resolve_commit", boom)
    monkeypatch.setattr(runner_git, "diff_text", boom)

    result = as_seat(store, "rev-1", "brief", {"item": task})
    assert is_ok(result), result
    assert produced["value"]["record"] in result["value"]["text"]
    assert produced["value"]["path"] in result["value"]["text"]


def test_an_item_outside_the_seats_reach_is_refused(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    result = as_seat(seated.store, seated.seat, "brief", {"item": role_store.b1})

    assert refusal(result) == ("PERMISSION_DENIED", "out_of_reach")