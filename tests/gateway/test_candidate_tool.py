"""``rail loop candidate`` records a commit any tool produced for an item."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.seats import SEAT_GRANTED
from helpers import (
    approve_picture,
    as_seat,
    edit_roles,
    records_of,
    refusal,
    restrict_code_write,
    run_cli,
    run_git,
    start_repo,
    write_candidate,
    write_record,
)


def head(root: Path, rev: str = "HEAD") -> str:
    return run_git(root, "rev-parse", rev).stdout.strip()


def commit_file(root: Path, rel: str, text: str, message: str = "change") -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", rel)
    run_git(root, "commit", "-m", message)
    return head(root)


def record(
    leaf_item: SimpleNamespace, actor: str = "dev", **args: Any
) -> dict[str, Any]:
    arguments = {"item": leaf_item.slug, "commit": "HEAD", **args}
    return invoke("candidate", arguments, store=leaf_item.store, actor=actor)


def test_a_store_only_commit_is_not_a_candidate(leaf_item: SimpleNamespace) -> None:
    store_rel = leaf_item.store.relative_to(leaf_item.root).as_posix()
    commit_file(leaf_item.root, f"{store_rel}/notes/note/only-store.md", "store\n")

    result = record(leaf_item)

    assert not is_ok(result), result
    assert result["error"]["details"]["reason"] == "store_only_commit"


def test_candidate_records_commit_base_and_files(leaf_item: SimpleNamespace) -> None:
    before = head(leaf_item.root)
    tip = commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    result = record(leaf_item)

    assert is_ok(result), result
    value = result["value"]
    assert value["commit"] == tip
    assert value["base"] == before
    assert value["files_changed"] == ["src/app.py"]
    assert value["files_truncated"] is False
    assert value["attempt"] == 1
    assert value["replayed"] is False
    stored = records_of(leaf_item.store, "candidate.recorded")
    assert [entry["id"] for entry in stored] == [value["record"]]
    assert stored[0]["actor"] == "dev"
    assert stored[0]["data"]["item"] == leaf_item.slug
    assert "VALUE = 2" in stored[0]["data"]["diff"]
    assert stored[0]["data"]["diff_truncated"] is False


def test_candidate_empty_diff_and_6k_truncation_snapshots(leaf_item: SimpleNamespace) -> None:
    tip = head(leaf_item.root)
    empty = invoke(
        "candidate",
        {"item": leaf_item.slug, "commit": tip, "base": tip},
        store=leaf_item.store,
        actor="dev",
    )
    assert is_ok(empty), empty
    empty_data = records_of(leaf_item.store, "candidate.recorded")[0]["data"]
    assert empty_data["diff"] == ""
    assert empty_data["diff_truncated"] is False

    big = "".join(f"LINE_{n} = {n}\n" for n in range(1500))
    commit_file(leaf_item.root, "src/big.py", big)
    truncated = record(leaf_item)
    assert is_ok(truncated), truncated
    trunc_data = [
        r["data"]
        for r in records_of(leaf_item.store, "candidate.recorded")
        if r["id"] == truncated["value"]["record"]
    ][0]
    assert len(trunc_data["diff"]) == 6000
    assert trunc_data["diff_truncated"] is True


def test_candidate_does_not_capture_diff_when_code_reach_refuses(
    seated: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_rail.runner import git as runner_git

    restrict_code_write(seated.store, ["src/**"])
    start_repo(seated)
    (seated.root / "docs" / "x.md").write_text("changed\n", encoding="utf-8")
    run_git(seated.root, "add", "docs/x.md")
    run_git(seated.root, "commit", "-m", "out of reach")

    def boom(*args: object, **kwargs: object) -> Any:
        raise AssertionError("diff_text must not run before reach checks pass")

    monkeypatch.setattr(runner_git, "diff_text", boom)
    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )
    assert refusal(result) == ("BUSINESS_RULE_VIOLATED", "code_reach")


def test_candidate_matches_short_sha_and_branch_name(leaf_item: SimpleNamespace) -> None:
    run_git(leaf_item.root, "checkout", "-b", "work")
    tip = commit_file(leaf_item.root, "src/app.py", "VALUE = 3\n")
    run_git(leaf_item.root, "checkout", "main")

    by_branch = record(leaf_item, commit="work")
    by_short = record(leaf_item, commit=tip[:9])

    assert is_ok(by_branch), by_branch
    assert is_ok(by_short), by_short
    assert by_branch["value"]["commit"] == tip
    assert by_short["value"]["replayed"] is True
    assert by_short["value"]["record"] == by_branch["value"]["record"]


def test_candidate_base_is_merge_base_for_a_branch(leaf_item: SimpleNamespace) -> None:
    fork = head(leaf_item.root)
    run_git(leaf_item.root, "checkout", "-b", "work")
    commit_file(leaf_item.root, "src/a.py", "A = 1\n", "first")
    tip = commit_file(leaf_item.root, "src/b.py", "B = 1\n", "second")
    run_git(leaf_item.root, "checkout", "main")
    advanced = commit_file(leaf_item.root, "README.md", "moved on\n", "main moves")
    assert advanced != fork

    result = record(leaf_item, commit="work")

    assert is_ok(result), result
    assert result["value"]["commit"] == tip
    assert result["value"]["base"] == fork
    assert result["value"]["files_changed"] == ["src/a.py", "src/b.py"]


def test_candidate_explicit_base_wins_over_the_default(leaf_item: SimpleNamespace) -> None:
    first = head(leaf_item.root)
    commit_file(leaf_item.root, "src/a.py", "A = 1\n", "first")
    commit_file(leaf_item.root, "src/b.py", "B = 1\n", "second")

    explicit = record(leaf_item, base=first)

    assert is_ok(explicit), explicit
    assert explicit["value"]["files_changed"] == ["src/a.py", "src/b.py"]
    assert explicit["value"]["base"] == first


def test_candidate_refuses_root_commit_without_base(leaf_item: SimpleNamespace) -> None:
    result = record(leaf_item)

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert "--base" in result["error"]["message"]
    assert records_of(leaf_item.store, "candidate.recorded") == []

    allowed = record(leaf_item, base=head(leaf_item.root))
    assert is_ok(allowed), allowed
    assert allowed["value"]["files_changed"] == []


@pytest.mark.parametrize("field", ["commit", "base"])
@pytest.mark.parametrize("bad", ["nope", "--all", "-1", "  "])
def test_candidate_refuses_unknown_and_option_like_revisions(
    leaf_item: SimpleNamespace, field: str, bad: str
) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    result = record(leaf_item, **{field: bad})

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert records_of(leaf_item.store, "candidate.recorded") == []


def add_task(store: Path, title: str, *relations: str) -> str:
    added = invoke(
        "add",
        {"preset": "task", "title": title, "rel": list(relations)},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    return added["value"]["slug"]


def test_candidate_refuses_unknown_container_finished_and_blocked_items(
    leaf_item: SimpleNamespace,
) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")
    waiting = add_task(leaf_item.store, "Waiting", f"depends_on={leaf_item.slug}")
    finished = add_task(leaf_item.store, "Finished", "part_of=send-invoice")
    shown = invoke("show", {"slug": finished}, store=leaf_item.store, actor="cto")
    dropped = invoke(
        "move",
        {"slug": finished, "status": "dropped", "expect": shown["value"]["doc"]["etag"]},
        store=leaf_item.store,
        actor="cto",
    )
    assert is_ok(dropped), dropped

    unknown = record(leaf_item, item="ghost")
    container = record(leaf_item, item="send-invoice")
    closed = record(leaf_item, item=finished)
    blocked = record(leaf_item, item=waiting)

    assert unknown["error"]["code"] == "NOT_FOUND", unknown
    assert refusal(container)[0] == "INVALID_STATE"
    assert refusal(closed)[0] == "INVALID_STATE"
    assert refusal(blocked)[0] == "INVALID_STATE"
    assert blocked["error"]["details"]["reason"] == f"depends_on:{leaf_item.slug}"
    assert records_of(leaf_item.store, "candidate.recorded") == []


def test_candidate_same_commit_returns_existing(leaf_item: SimpleNamespace) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    first = record(leaf_item)
    again = record(leaf_item, actor="other")
    commit_file(leaf_item.root, "src/app.py", "VALUE = 3\n")
    second = record(leaf_item)

    assert first["value"]["replayed"] is False
    assert again["value"]["replayed"] is True
    assert again["value"]["record"] == first["value"]["record"]
    assert again["value"]["attempt"] == 1
    assert second["value"]["replayed"] is False
    assert second["value"]["attempt"] == 2
    assert len(records_of(leaf_item.store, "candidate.recorded")) == 2


def test_candidate_key_replays(leaf_item: SimpleNamespace) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    first = record(leaf_item, key="k-1")
    again = record(leaf_item, key="k-1")

    assert is_ok(first) and is_ok(again), (first, again)
    assert again["value"]["record"] == first["value"]["record"]
    assert again["value"]["commit"] == first["value"]["commit"]
    assert again["value"]["replayed"] is True
    assert len(records_of(leaf_item.store, "candidate.recorded")) == 1


def test_candidate_refused_before_approval(unapproved_store: Path) -> None:
    result = invoke(
        "candidate",
        {"item": "send-invoice", "commit": "HEAD"},
        store=unapproved_store,
        actor="dev",
    )

    assert refusal(result) == ("INVALID_STATE", "approval_missing")


def test_candidate_files_capped_at_500(leaf_item: SimpleNamespace) -> None:
    folder = leaf_item.root / "bulk"
    folder.mkdir()
    for number in range(501):
        (folder / f"f{number:03}.txt").write_text(str(number), encoding="utf-8")
    run_git(leaf_item.root, "add", "bulk")
    run_git(leaf_item.root, "commit", "-m", "bulk")

    result = record(leaf_item)

    assert is_ok(result), result
    assert len(result["value"]["files_changed"]) == 500
    assert result["value"]["files_truncated"] is True
    stored = records_of(leaf_item.store, "candidate.recorded")[0]["data"]
    assert stored["files_truncated"] is True


def test_candidate_commit_made_in_another_worktree(
    leaf_item: SimpleNamespace, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    run_git(leaf_item.root, "worktree", "add", "-b", "tool-work", str(other))
    tip = commit_file(other, "src/app.py", "VALUE = 9\n")

    result = record(leaf_item, commit=tip)

    assert is_ok(result), result
    assert result["value"]["commit"] == tip
    assert result["value"]["files_changed"] == ["src/app.py"]


def test_candidate_usage_duration_and_free_text_are_stored(
    leaf_item: SimpleNamespace,
) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    result = record(
        leaf_item,
        usage=["input_tokens=12", "cost_usd=0.5", "x=unknown"],
        duration="3.5",
        model="any-model",
        harness="any-tool 1.0",
    )

    assert is_ok(result), result
    data = records_of(leaf_item.store, "candidate.recorded")[0]["data"]
    assert data["usage"] == {"input_tokens": 12, "cost_usd": 0.5, "x": "unknown"}
    assert data["duration_s"] == 3.5
    assert data["model"] == "any-model"
    assert data["harness"] == "any-tool 1.0"


@pytest.mark.parametrize(
    "extra",
    [
        {"usage": ["Input=1"]},
        {"usage": ["input_tokens"]},
        {"usage": ["input_tokens=-5"]},
        {"usage": ["input_tokens=lots"]},
        {"usage": ["input_tokens=1", "input_tokens=2"]},
        {"duration": "-1"},
        {"duration": "soon"},
        {"model": "m" * 201},
        {"harness": "h" * 201},
    ],
)
def test_candidate_refuses_bad_usage_duration_and_text(
    leaf_item: SimpleNamespace, extra: dict[str, Any]
) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    result = record(leaf_item, **extra)

    assert refusal(result)[0] == "VALIDATION_FAILED"
    assert records_of(leaf_item.store, "candidate.recorded") == []


def test_candidate_pins_the_named_profile(leaf_item: SimpleNamespace) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    result = record(leaf_item, profile="implementer")
    unknown = record(leaf_item, profile="no-such-profile", key="other")

    assert is_ok(result), result
    data = records_of(leaf_item.store, "candidate.recorded")[0]["data"]
    assert data["profile"] == "implementer"
    assert isinstance(data["profile_version"], int)
    assert isinstance(data["skills"], list)
    assert refusal(unknown)[0] == "VALIDATION_FAILED"


def test_candidate_cli_alias_and_flags(leaf_item: SimpleNamespace) -> None:
    commit_file(leaf_item.root, "src/app.py", "VALUE = 2\n")

    proc = run_cli(
        leaf_item.root,
        "--actor",
        "dev",
        "loop",
        "candidate",
        leaf_item.slug,
        "--commit",
        "HEAD",
        "--usage",
        "input_tokens=7",
        "--usage",
        "output_tokens=3",
        "--duration",
        "2",
        store=leaf_item.store,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    body = json.loads(proc.stdout)
    assert body["value"]["files_changed"] == ["src/app.py"]
    data = records_of(leaf_item.store, "candidate.recorded")[0]["data"]
    assert data["usage"] == {"input_tokens": 7, "output_tokens": 3}


def test_candidate_records_seat_and_role(seated: SimpleNamespace) -> None:
    start_repo(seated)
    commit_file(seated.root, "src/app.py", "VALUE = 2\n")

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert is_ok(result), result
    stored = records_of(seated.store, "candidate.recorded")[0]
    assert stored["actor"] == seated.seat
    assert stored["data"]["seat"] == seated.seat
    assert stored["data"]["seat_role"] == "implementer"


def test_candidate_refuses_code_outside_seat_reach(seated: SimpleNamespace) -> None:
    restrict_code_write(seated.store, ["src/**"])
    start_repo(seated)
    (seated.root / "src" / "ok.py").write_text("OK = 1\n", encoding="utf-8")
    (seated.root / "docs" / "x.md").write_text("changed\n", encoding="utf-8")
    run_git(seated.root, "add", "src/ok.py", "docs/x.md")
    run_git(seated.root, "commit", "-m", "both")

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert refusal(result) == ("BUSINESS_RULE_VIOLATED", "code_reach")
    assert result["error"]["details"]["paths"] == ["docs/x.md"]
    assert result["error"]["details"]["reach"] == ["src/**"]
    assert records_of(seated.store, "candidate.recorded") == []


def test_candidate_refuses_a_rename_out_of_seat_reach(seated: SimpleNamespace) -> None:
    restrict_code_write(seated.store, ["src/**"])
    start_repo(seated)
    run_git(seated.root, "mv", "src/app.py", "docs/app.py")
    run_git(seated.root, "commit", "-m", "move out of reach")

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert refusal(result) == ("BUSINESS_RULE_VIOLATED", "code_reach")
    assert "docs/app.py" in result["error"]["details"]["paths"]


def test_candidate_refuses_a_rename_into_reach_that_leaves_an_outside_path(
    seated: SimpleNamespace,
) -> None:
    restrict_code_write(seated.store, ["src/**"])
    start_repo(seated)
    run_git(seated.root, "mv", "docs/x.md", "src/x.md")
    run_git(seated.root, "commit", "-m", "pull a file into reach")

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert refusal(result) == ("BUSINESS_RULE_VIOLATED", "code_reach")
    assert result["error"]["details"]["paths"] == ["docs/x.md"]


def test_candidate_inside_seat_reach_is_accepted(seated: SimpleNamespace) -> None:
    restrict_code_write(seated.store, ["src/**"])
    start_repo(seated)
    commit_file(seated.root, "src/ok.py", "OK = 1\n")

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert is_ok(result), result


def test_candidate_enforces_the_roles_independence(seated: SimpleNamespace) -> None:
    def edit(roles: dict[str, Any]) -> None:
        roles["implementer"]["independent_of"] = ["produce"]

    edit_roles(seated.store, edit)
    start_repo(seated)
    commit_file(seated.root, "src/ok.py", "OK = 1\n")
    write_candidate(
        seated.store,
        "01JCAN0000000000000000001",
        seated.task,
        "b" * 40,
        seat=seated.seat,
    )

    result = as_seat(
        seated.store, seated.seat, "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert refusal(result) == ("PERMISSION_DENIED", "independence_violation")


def test_candidate_is_not_in_a_reviewers_role(
    seated: SimpleNamespace, role_store: SimpleNamespace
) -> None:
    write_record(
        seated.store,
        "seat-rev-1",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "rev-1",
            "role": "reviewer",
            "anchor": seated.task,
            "granted_by": role_store.lead,
        },
        at="2026-11-10T10:04:00Z",
        actor=role_store.lead,
    )
    start_repo(seated)
    commit_file(seated.root, "src/ok.py", "OK = 1\n")

    result = as_seat(
        seated.store, "rev-1", "candidate", {"item": seated.task, "commit": "HEAD"}
    )

    assert refusal(result)[0] == "PERMISSION_DENIED"
    assert result["error"]["details"]["reason"] == "method_not_in_role"


def test_a_fix_item_can_take_a_candidate_while_verifying(
    tmp_path: Path, answers: Path
) -> None:
    """``verifying`` is off the queue but still open, so work may be recorded."""
    project = tmp_path / "project"
    project.mkdir()
    payload = {**json.loads(answers.read_text(encoding="utf-8")), "pack": "scrum"}
    scrum_answers = tmp_path / "scrum-answers.json"
    scrum_answers.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    created = invoke(
        "init",
        {
            "dir": str(project),
            "answers_file": str(scrum_answers),
            "pack": "scrum",
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    assert is_ok(created), created
    store = project / "context-rail"
    assert is_ok(approve_picture(store))
    added = invoke(
        "add", {"preset": "fix", "title": "Broken thing"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    run_git(project, "init", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", "picture")
    commit_file(project, "src/fix.py", "FIXED = True\n")

    for status in ("fixing", "verifying"):
        etag = invoke("show", {"slug": "broken-thing"}, store=store)["value"]["doc"][
            "etag"
        ]
        moved = invoke(
            "move",
            {"slug": "broken-thing", "status": status, "expect": etag},
            store=store,
            actor="cto",
        )
        assert is_ok(moved), moved

    candidate = invoke(
        "candidate",
        {"item": "broken-thing", "commit": "HEAD"},
        store=store,
        actor="cto",
    )

    assert is_ok(candidate), candidate
