"""``rail loop review`` records a verdict on a candidate any tool produced."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import (
    as_human,
    as_seat,
    edit_roles,
    records_of,
    refusal,
    run_cli,
    run_git,
    touch_doc,
    write_record,
)
from perf.gen import make_store

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.model.seats import SEAT_GRANTED
from context_rail.store import query
from context_rail.store.index import Index


@pytest.fixture
def picture_answers(picture_answers: dict[str, Any], request: pytest.FixtureRequest) -> dict[str, Any]:
    if getattr(request, "param", None) == "model-optional":
        picture_answers["charter"]["reviewer_different_model"] = False
    return picture_answers


def commit_file(root: Path, text: str) -> str:
    path = root / "src" / "app.py"
    path.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", "src/app.py")
    run_git(root, "commit", "-m", "change")
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def candidate(
    leaf_item: SimpleNamespace, text: str = "VALUE = 2\n", *, actor: str = "dev", **args: Any
) -> str:
    tip = commit_file(leaf_item.root, text)
    recorded = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, **args},
        store=leaf_item.store,
        actor=actor,
    )
    assert is_ok(recorded), recorded
    return tip


def review(
    leaf_item: SimpleNamespace, actor: str = "qa", **args: Any
) -> dict[str, Any]:
    arguments = {
        "item": leaf_item.slug,
        "commit": "HEAD",
        "verdict": "approve",
        "model": "model-b",
        **args,
    }
    return invoke("review", arguments, store=leaf_item.store, actor=actor)


def test_review_requires_recorded_candidate(leaf_item: SimpleNamespace) -> None:
    commit_file(leaf_item.root, "VALUE = 2\n")

    result = review(leaf_item)

    assert refusal(result) == ("INVALID_STATE", "no_candidate")
    assert "rail loop candidate" in result["error"]["message"]
    assert records_of(leaf_item.store, "review.verdict") == []


def test_review_accepts_short_sha_and_branch_name(leaf_item: SimpleNamespace) -> None:
    run_git(leaf_item.root, "checkout", "-b", "work")
    tip = candidate(leaf_item, model="model-a")

    by_short = review(leaf_item, commit=tip[:8])
    by_branch = review(leaf_item, commit="work")

    assert is_ok(by_short), by_short
    assert is_ok(by_branch), by_branch
    assert by_short["value"]["commit"] == tip
    assert by_branch["value"]["commit"] == tip


def test_review_approve_records_verdict_bound_to_candidate(
    leaf_item: SimpleNamespace,
) -> None:
    tip = candidate(leaf_item, model="model-a")
    [recorded] = records_of(leaf_item.store, "candidate.recorded")

    result = review(leaf_item, harness="claude-code")

    assert is_ok(result), result
    assert result["value"]["verdict"] == "approve"
    [stored] = records_of(leaf_item.store, "review.verdict")
    assert stored["actor"] == "qa"
    assert stored["about"] == [leaf_item.slug]
    data = stored["data"]
    assert data["candidate_commit"] == tip
    assert data["candidate_record"] == recorded["id"]
    assert data["reviewer_actor"] == "qa"
    assert data["implementer_actor"] == "dev"
    assert data["implementer_model"] == "model-a"
    assert data["reviewer_model"] == "model-b"
    assert data["harness"] == "claude-code"
    assert data["verdict"] == "approve"
    assert data["findings"] == [] and data["evidence"] == []
    assert data["implementation_run"] is None
    index = Index.open(leaf_item.store, load_pack("basic", leaf_item.store))
    try:
        index.sync()
        found = query.review_for(index, tip)
    finally:
        index.close()
    assert found is not None
    assert found["reviewer"] == "qa" and found["verdict"] == "approve"


def test_review_reject_records_verdict(leaf_item: SimpleNamespace, tmp_path: Path) -> None:
    candidate(leaf_item, model="model-a")
    notes = tmp_path / "notes.md"
    notes.write_text("The loop never ends.\n", encoding="utf-8")

    result = review(leaf_item, verdict="reject", notes_file=str(notes))

    assert is_ok(result), result
    assert result["value"]["verdict"] == "reject"
    [stored] = records_of(leaf_item.store, "review.verdict")
    assert stored["data"]["verdict"] == "reject"
    assert stored["data"]["notes"] == "The loop never ends.\n"


def test_review_by_candidate_actor_refused(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")

    result = review(leaf_item, actor="dev")

    assert refusal(result) == ("PERMISSION_DENIED", "separation")
    assert records_of(leaf_item.store, "review.verdict") == []


def test_review_model_rule_on(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")
    candidate_without_model = candidate(leaf_item, "VALUE = 3\n")

    no_models = review(leaf_item, commit=candidate_without_model, model=None)
    reviewer_missing = review(leaf_item, commit="HEAD~1", model=None)
    candidate_missing = review(leaf_item, commit=candidate_without_model)
    same_model = review(leaf_item, commit="HEAD~1", model="model-a")
    different = review(leaf_item, commit="HEAD~1", model="model-b")

    assert refusal(no_models) == ("PERMISSION_DENIED", "model_required")
    assert refusal(reviewer_missing) == ("PERMISSION_DENIED", "model_required")
    assert refusal(candidate_missing) == ("PERMISSION_DENIED", "model_required")
    assert "candidate" in candidate_missing["error"]["message"]
    assert "reviewer" in reviewer_missing["error"]["message"]
    assert refusal(same_model) == ("PERMISSION_DENIED", "separation")
    assert is_ok(different), different


@pytest.mark.parametrize("picture_answers", ["model-optional"], indirect=True)
def test_review_without_models_when_rule_off(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item)

    result = review(leaf_item, model=None)

    assert is_ok(result), result
    [stored] = records_of(leaf_item.store, "review.verdict")
    assert "reviewer_model" not in stored["data"] or stored["data"]["reviewer_model"] is None


def declare_review_check(leaf_item: SimpleNamespace, tmp_path: Path, slug: str) -> None:
    spec = tmp_path / f"{slug}.json"
    spec.write_text(
        json.dumps({"schema": 1, "mode": "review", "acceptance": False}),
        encoding="utf-8",
    )
    declared = invoke(
        "check",
        {"slug": slug, "target": leaf_item.slug, "spec_file": str(spec), "expect": "absent"},
        store=leaf_item.store,
        actor="cto",
    )
    assert is_ok(declared), declared


def test_review_verifies_review_mode_checks(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    declare_review_check(leaf_item, tmp_path, "peer-review")
    candidate(leaf_item, model="model-a")
    before = invoke("verify", {"target": "peer-review"}, store=leaf_item.store, actor="cto")
    assert before["value"]["passed"] is False

    result = review(leaf_item)

    assert is_ok(result), result
    assert result["value"]["checks_verified"] == ["peer-review"]
    checked = invoke("verify", {"target": "peer-review"}, store=leaf_item.store, actor="cto")
    assert is_ok(checked), checked
    assert checked["value"]["passed"] is True
    entry = checked["value"]["verdicts"][0]
    assert entry["freshness"] == "fresh" and entry["record"]


def test_a_review_check_is_declared_when_the_item_has_none(
    leaf_item: SimpleNamespace,
) -> None:
    candidate(leaf_item, model="model-a")

    result = review(leaf_item)

    assert is_ok(result), result
    slug = f"review-{leaf_item.slug}"
    assert result["value"]["checks_verified"] == [slug]
    shown = invoke("show", {"slug": slug}, store=leaf_item.store)["value"]
    assert shown["doc"]["fields"]["mode"] == "review"
    assert shown["freshness"] == "fresh"


def test_the_review_check_verdict_only_goes_stale_when_the_check_changes(
    leaf_item: SimpleNamespace,
) -> None:
    candidate(leaf_item, model="model-a")
    assert is_ok(review(leaf_item))
    slug = f"review-{leaf_item.slug}"
    store = leaf_item.store
    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "fresh"

    again = invoke("verify", {"target": slug}, store=store, actor="cto")
    assert is_ok(again), again
    assert again["value"]["verdicts"][0]["freshness"] == "fresh"
    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "fresh"

    touch_doc(store, f"checks/{slug}.md", timeout_s=60)

    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "stale"
    rechecked = invoke("verify", {"target": slug}, store=store, actor="cto")
    assert is_ok(rechecked), rechecked
    assert rechecked["value"]["passed"] is True
    assert rechecked["value"]["verdicts"][0]["freshness"] == "fresh"


def test_the_declared_review_check_slug_avoids_a_used_note_slug(
    leaf_item: SimpleNamespace,
) -> None:
    store = leaf_item.store
    note = invoke(
        "add", {"preset": "note", "title": f"review-{leaf_item.slug}"}, store=store, actor="cto"
    )
    assert is_ok(note), note
    candidate(leaf_item, model="model-a")

    result = review(leaf_item)

    assert is_ok(result), result
    assert (store / "checks" / f"review-{leaf_item.slug}-2.md").is_file()
    assert not (store / "checks" / f"review-{leaf_item.slug}.md").exists()


def test_review_notes_over_4000_chars_refused(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    candidate(leaf_item, model="model-a")
    notes = tmp_path / "long.md"
    notes.write_text("x" * 4001, encoding="utf-8")

    result = review(leaf_item, notes_file=str(notes))

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert records_of(leaf_item.store, "review.verdict") == []


def test_review_refuses_a_missing_notes_file_and_a_bad_verdict(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    candidate(leaf_item, model="model-a")

    missing = review(leaf_item, notes_file=str(tmp_path / "ghost.md"))
    bad = review(leaf_item, verdict="maybe")

    assert refusal(missing)[0] == "VALIDATION_FAILED"
    assert refusal(bad)[0] == "VALIDATION_FAILED"
    assert records_of(leaf_item.store, "review.verdict") == []


def test_review_key_replays(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")

    first = review(leaf_item, key="rv-1")
    second = review(leaf_item, key="rv-1")

    assert is_ok(first) and is_ok(second), (first, second)
    assert first["value"]["record"] == second["value"]["record"]
    assert len(records_of(leaf_item.store, "review.verdict")) == 1


def test_review_cli_alias_and_flags(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, model="model-a")

    proc = run_cli(
        leaf_item.root,
        "--actor",
        "qa",
        "loop",
        "review",
        leaf_item.slug,
        "--commit",
        "HEAD",
        "--verdict",
        "approve",
        "--model",
        "model-b",
        store=leaf_item.store,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert json.loads(proc.stdout)["value"]["verdict"] == "approve"


def test_legacy_review_record_still_projects(tmp_path: Path) -> None:
    store = make_store(tmp_path, 10)
    write_record(
        store,
        "01JREV00000000000000000001",
        "review.verdict",
        {
            "schema": 1,
            "item": "t0",
            "implementation_run": "01JRUN00000000000000000001",
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "model-a",
            "reviewer_model": "model-b",
            "candidate_commit": "a" * 40,
            "verdict": "approve",
            "findings": ["fine"],
            "evidence": [],
            "profile_version": 2,
        },
        about=("t0",),
    )
    write_record(
        store,
        "01JREV00000000000000000002",
        "review.verdict",
        {
            "schema": 1,
            "item": "t1",
            "reviewer_actor": "qa",
            "candidate_commit": "b" * 40,
            "verdict": "reject",
        },
        about=("t1",),
        at="2026-10-28T01:00:00Z",
    )

    index = Index.open(store, load_pack("basic", store))
    try:
        index.sync()
        old = query.review_for(index, "a" * 40)
        new = query.review_for(index, "b" * 40)
    finally:
        index.close()

    assert old is not None and old["run"] == "01JRUN00000000000000000001"
    assert old["findings"] == ["fine"]
    assert new is not None and new["run"] is None and new["verdict"] == "reject"


def seat_data(seat: str, role: str, anchor: str, granted_by: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema": 1,
        "seat": seat,
        "role": role,
        "anchor": anchor,
        "granted_by": granted_by,
        **extra,
    }


@pytest.fixture
def seated(role_store: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    """A role store in a checkout with a task, an implementer and a reviewer seat on it."""
    root = tmp_path
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    with as_human():
        added = invoke(
            "add",
            {"preset": "task", "title": "Seated", "rel": [f"part_of={role_store.o1}"]},
            store=role_store.store,
            actor=None,
        )
    assert is_ok(added), added
    task = added["value"]["slug"]
    for number, (seat, role) in enumerate((("impl-1", "implementer"), ("rev-1", "reviewer"))):
        write_record(
            role_store.store,
            f"seat-{seat}",
            SEAT_GRANTED,
            seat_data(seat, role, task, role_store.lead),
            at=f"2026-11-10T10:0{3 + number}:00Z",
            actor=role_store.lead,
        )
    run_git(root, "init", "-b", "main")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "picture")
    return SimpleNamespace(store=role_store.store, root=root, task=task, lead=role_store.lead)


def seat_candidate(seated: SimpleNamespace, **args: Any) -> str:
    tip = commit_file(seated.root, "VALUE = 2\n")
    result = as_seat(
        seated.store,
        "impl-1",
        "candidate",
        {"item": seated.task, "commit": tip, "model": "model-a", **args},
    )
    assert is_ok(result), result
    return tip


def test_review_in_role_mode_records_seat_names(seated: SimpleNamespace) -> None:
    tip = seat_candidate(seated)

    result = as_seat(
        seated.store,
        "rev-1",
        "review",
        {"item": seated.task, "commit": tip, "verdict": "approve", "model": "model-b"},
    )

    assert is_ok(result), result
    [stored] = records_of(seated.store, "review.verdict")
    assert stored["actor"] == "rev-1"
    assert stored["data"]["reviewer_actor"] == "rev-1"
    assert stored["data"]["implementer_actor"] == "impl-1"


def test_review_is_not_in_an_implementers_role(seated: SimpleNamespace) -> None:
    tip = seat_candidate(seated)

    result = as_seat(
        seated.store,
        "impl-1",
        "review",
        {"item": seated.task, "commit": tip, "verdict": "approve", "model": "model-b"},
    )

    assert refusal(result)[0] == "PERMISSION_DENIED"
    assert result["error"]["details"]["reason"] == "method_not_in_role"


def test_review_independence_by_profile_refused(seated: SimpleNamespace) -> None:
    tip = seat_candidate(seated, profile="reviewer")
    write_record(
        seated.store,
        "seat-rev-2",
        SEAT_GRANTED,
        seat_data("rev-2", "reviewer", seated.task, seated.lead, profile="reviewer"),
        at="2026-11-10T10:09:00Z",
        actor=seated.lead,
    )

    result = as_seat(
        seated.store,
        "rev-2",
        "review",
        {"item": seated.task, "commit": tip, "verdict": "approve", "model": "model-b"},
    )

    assert refusal(result) == ("PERMISSION_DENIED", "independence_violation")
    assert records_of(seated.store, "review.verdict") == []

def test_review_needs_the_preset_in_can_review(seated: SimpleNamespace) -> None:
    def narrow(roles: dict[str, Any]) -> None:
        roles["reviewer"]["can"]["review"] = ["sprint"]

    edit_roles(seated.store, narrow)
    tip = seat_candidate(seated)

    result = as_seat(
        seated.store,
        "rev-1",
        "review",
        {"item": seated.task, "commit": tip, "verdict": "approve", "model": "model-b"},
    )

    assert refusal(result) == ("PERMISSION_DENIED", "preset_not_in_can_review")
