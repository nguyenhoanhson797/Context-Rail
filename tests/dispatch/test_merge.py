"""Gated rebase and fast-forward: proofs, closeout, conflicts and replay."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from helpers import (
    advance_default_branch,
    default_head,
    item_etag,
    item_status,
    latest_record,
    merge_call,
    records_of,
    review_call,
    run_git,
)

RESULT_KEYS = {
    "commit",
    "branch",
    "merged",
    "pushed",
    "closed",
    "record",
    "escalation",
}


def git_text(where: Path, *args: str) -> str:
    return run_git(where, *args).stdout.rstrip("\r\n")


def implementer_attempts(run: SimpleNamespace) -> int:
    """Attempts the implementer runs of one Item already spent."""
    total = 0
    for record in records_of(run.store, "run.completed"):
        data = record["data"]
        if data.get("item") == run.item and data.get("role") == "implementer":
            total += len(data.get("attempts") or ())
    return total


def no_merge_commits(project: Path) -> bool:
    return git_text(project, "log", "--merges", "--oneline") == ""


def is_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    try:
        run_git(project, "merge-base", "--is-ancestor", ancestor, descendant)
    except RuntimeError:
        return False
    return True


# ── the gated fast-forward ────────────────────────────────────────────────


def test_clean_gated_merge_fast_forwards_and_closes(
    verified_run: SimpleNamespace,
) -> None:
    r = merge_call(verified_run, "m1")

    assert r["ok"], r
    value = r["value"]
    assert set(value) == RESULT_KEYS
    assert value["merged"] is True
    assert value["pushed"] is False
    assert value["closed"] == verified_run.item
    assert value["branch"] == "main"
    assert value["commit"] == default_head(verified_run)
    assert value["escalation"] is None
    assert item_status(verified_run) == "done"

    merged = records_of(verified_run.store, "merge.completed")
    assert len(merged) == 1
    assert merged[0]["about"] == [verified_run.item]
    assert merged[0]["data"]["candidate_commit"] == value["commit"]
    assert value["record"] == merged[0]["id"]

    run_git(verified_run.project, "merge-base", "--is-ancestor", value["commit"], "HEAD")
    assert no_merge_commits(verified_run.project)
    history = git_text(verified_run.project, "log", "--format=%s")
    assert history.count("fake droid candidate") == 1


def test_rejected_review_blocks_merge(rejected_run: SimpleNamespace) -> None:
    r = merge_call(rejected_run, "m2")

    assert r["ok"] is False, r
    assert r["error"]["code"] in {"BUSINESS_RULE_VIOLATED", "INVALID_STATE"}
    assert default_head(rejected_run) == rejected_run.base
    assert records_of(rejected_run.store, "merge.completed") == []
    assert item_status(rejected_run) != "done"


def test_default_branch_movement_forces_recheck_and_rereview(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    fake_droid.set_behaviors(["review_approve"])
    first = review_call(verified_run, "bound-before-movement")
    assert first["ok"], first
    bound = latest_record(verified_run.store, "review.verdict")["data"]["candidate_commit"]
    assert bound == verified_run.candidate

    advance_default_branch(verified_run, "unrelated.txt")
    assert default_head(verified_run) != bound

    fake_droid.set_behaviors(["review_approve"])
    r = merge_call(verified_run, "m4")

    assert r["ok"], r
    assert r["value"]["merged"] is True
    assert default_head(verified_run) == r["value"]["commit"]
    assert r["value"]["commit"] != bound
    assert (
        latest_record(verified_run.store, "check.verdict")["data"]["candidate_commit"]
        == r["value"]["commit"]
    )
    assert (
        latest_record(verified_run.store, "review.verdict")["data"]["candidate_commit"]
        == r["value"]["commit"]
    )
    assert no_merge_commits(verified_run.project)
    subjects = git_text(verified_run.project, "log", "--format=%s")
    assert "human: unrelated.txt" in subjects
    assert "fake droid candidate" in subjects


def test_a_review_bound_to_another_commit_blocks_merge(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    fake_droid.set_behaviors(["review_approve"])
    approved = review_call(verified_run, "bound-to-c1")
    assert approved["ok"], approved
    review = latest_record(verified_run.store, "review.verdict")
    assert review["data"]["candidate_commit"] == verified_run.candidate

    worktree = verified_run.dispatch_worktree
    (worktree / "late-change.txt").write_text("late\n", encoding="utf-8", newline="\n")
    run_git(worktree, "add", "late-change.txt")
    run_git(worktree, "commit", "--amend", "--no-edit")
    amended = git_text(worktree, "rev-parse", "HEAD")
    assert amended != verified_run.candidate

    head_before = default_head(verified_run)
    r = merge_call(verified_run, "m-bound")

    assert r["ok"] is False, r
    assert r["error"]["code"] in {"BUSINESS_RULE_VIOLATED", "INVALID_STATE"}
    assert default_head(verified_run) == head_before
    assert records_of(verified_run.store, "merge.completed") == []
    error = json.dumps(r["error"])
    assert amended in error or verified_run.candidate in error
    assert "review" in error.lower() or "approval" in error.lower()


# ── the canonical checkout ────────────────────────────────────────────────


def test_dirty_checkout_refuses_and_keeps_changes(verified_run: SimpleNamespace) -> None:
    readme = verified_run.project / "README.md"
    mine = b"my own change\n"
    readme.write_bytes(mine)
    (verified_run.project / "unrelated.txt").write_text(
        "staged\n", encoding="utf-8", newline="\n"
    )
    run_git(verified_run.project, "add", "unrelated.txt")
    head_before = default_head(verified_run)

    r = merge_call(verified_run, "m3")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert default_head(verified_run) == head_before
    assert readme.read_bytes() == mine
    assert git_text(verified_run.project, "stash", "list") == ""
    status = git_text(verified_run.project, "status", "--porcelain")
    assert "A  unrelated.txt" in status
    assert " M README.md" in status or "M  README.md" in status


def test_a_checkout_off_the_default_branch_is_refused(
    verified_run: SimpleNamespace,
) -> None:
    run_git(verified_run.project, "checkout", "-b", "side")

    r = merge_call(verified_run, "m-side")

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["current"] == "side"


def _rooted_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A rooted store checkout, the observed default HEAD and a candidate commit."""
    root = tmp_path / "rooted-merge"
    root.mkdir()
    (root / "rail.yaml").write_text(
        "schema: 3\npack: small\n"
        "placement:\n"
        "  item: items/{preset}/{slug}.md\n"
        "  note: notes/{preset}/{slug}.md\n"
        "  check: checks/{slug}.md\n"
        "  record: records/{yyyy}-{mm}/{id}.json\n"
        "  actor: workspace/profiles/{slug}/profile.md\n"
        "  policy: policies/{slug}.md\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "items" / "task").mkdir(parents=True)
    (root / "items" / "task" / "a.md").write_text("a\n", encoding="utf-8", newline="\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".rail-local/\n", encoding="utf-8", newline="\n")
    run_git(root, "init", "-b", "main")
    # Line-ending normalization would report a restored file as modified, which
    # would make the clean direction prove nothing about the ownership predicate.
    run_git(root, "config", "core.autocrlf", "false")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "base")
    observed = run_git(root, "rev-parse", "HEAD").stdout.strip()

    run_git(root, "checkout", "-b", "candidate")
    (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    run_git(root, "commit", "-am", "candidate work")
    candidate = run_git(root, "rev-parse", "HEAD").stdout.strip()
    run_git(root, "checkout", "main")

    return root, observed, candidate


def test_a_rooted_checkout_refuses_a_human_change_and_allows_a_clean_merge(
    tmp_path: Path,
) -> None:
    """``--root .`` must not disable the fast-forward dirty-checkout refusal."""
    from context_rail.gateway.handlers.dispatch.closeout import _fast_forward
    from context_rail.model.manifest import load_manifest
    from context_rail.store.location import store_prefixes
    from context_rail.store.write import WriteRefused

    root, observed, candidate = _rooted_repo(tmp_path)
    prefixes = store_prefixes(root, root, load_manifest(root))

    mine = b"VALUE = 9\n"
    (root / "src" / "app.py").write_bytes(mine)
    (root / "items" / "task" / "b.md").write_text("b\n", encoding="utf-8", newline="\n")

    with pytest.raises(WriteRefused) as refused:
        _fast_forward(
            None, root=root, candidate=candidate, observed=observed, store_paths=prefixes
        )
    assert refused.value.code == "INVALID_STATE"
    assert "src/app.py" in refused.value.details["paths"]
    assert run_git(root, "rev-parse", "HEAD").stdout.strip() == observed
    assert (root / "src" / "app.py").read_bytes() == mine

    (root / "src" / "app.py").write_bytes(b"VALUE = 1\n")
    merged = _fast_forward(
        None, root=root, candidate=candidate, observed=observed, store_paths=prefixes
    )
    assert merged == candidate
    assert (root / "src" / "app.py").read_bytes() == b"VALUE = 2\n"


# ── conflicts ─────────────────────────────────────────────────────────────


def test_one_conflict_continuation_resolves_and_merges(
    conflicting_run: SimpleNamespace, fake_droid: Any
) -> None:
    before = implementer_attempts(conflicting_run)
    fake_droid.set_behaviors(["merge_resolve", "review_approve"])

    r = merge_call(conflicting_run, "m-conflict")

    assert r["ok"], r
    assert r["value"]["merged"] is True
    assert r["value"]["closed"] == conflicting_run.item
    assert default_head(conflicting_run) == r["value"]["commit"]
    assert no_merge_commits(conflicting_run.project)

    launches = fake_droid.log()
    continuations = [row for row in launches if "-s" in row["argv"]]
    assert len(continuations) == 1
    assert "fake-droid-work.txt" in continuations[0]["prompt"]
    assert "conflict" in continuations[0]["prompt"].lower()

    assert implementer_attempts(conflicting_run) == before + 1
    runs = {
        record["id"]: record
        for record in records_of(conflicting_run.store, "run.completed")
        if record["data"].get("role") == "implementer"
        and record["data"].get("dispatch_id") == conflicting_run.dispatch_id
    }
    original = runs[conflicting_run.run]
    continuations_run = [row for key, row in runs.items() if key != conflicting_run.run]
    assert len(continuations_run) == 1
    continuation = continuations_run[0]
    assert continuation["data"]["attempts"][0]["session_id"] == original["data"]["session_id"]
    assert continuation["data"]["candidate_commit"] == r["value"]["commit"]
    assert continuation["data"]["report"]


def test_second_conflict_escalates_and_preserves_state(
    conflicting_run: SimpleNamespace, fake_droid: Any
) -> None:
    before = implementer_attempts(conflicting_run)
    fake_droid.set_behaviors(["merge_conflict"])

    r = merge_call(conflicting_run, "m-conflict-again")

    assert r["ok"], r
    value = r["value"]
    assert value["merged"] is False
    assert value["closed"] is None
    assert value["escalation"] is not None
    assert not is_ancestor(conflicting_run.project, conflicting_run.candidate, "HEAD")
    assert item_status(conflicting_run) != "done"
    assert implementer_attempts(conflicting_run) == before + 1

    escalated = records_of(conflicting_run.store, "merge.escalated")
    assert len(escalated) == 1
    assert escalated[0]["about"] == [conflicting_run.item]
    assert escalated[0]["data"]["conflict_files"] == ["fake-droid-work.txt"]
    assert value["escalation"]["record"] == escalated[0]["id"]

    worktree = Path(value["escalation"]["worktree"])
    assert worktree == conflicting_run.dispatch_worktree
    assert worktree.is_dir()
    status = git_text(worktree, "status", "--porcelain")
    assert "fake-droid-work.txt" in status
    assert status.startswith(("UU", "AA"))
    tip = git_text(conflicting_run.project, "rev-parse", conflicting_run.dispatch_branch)
    assert tip == conflicting_run.candidate
    assert records_of(conflicting_run.store, "merge.completed") == []


# ── replay and lockout ────────────────────────────────────────────────────


def test_replay_never_duplicates_the_merge(verified_run: SimpleNamespace) -> None:
    first = merge_call(verified_run, "m-replay")
    assert first["ok"], first
    assert first["value"]["merged"] is True
    head = default_head(verified_run)

    verified_run.etag = item_etag(verified_run)
    again = merge_call(verified_run, "m-replay")
    assert again["ok"], again
    assert again["value"] == first["value"]

    verified_run.etag = item_etag(verified_run)
    other_key = merge_call(verified_run, "m-reconcile")
    assert other_key["ok"], other_key
    assert other_key["value"]["merged"] is True
    assert other_key["value"]["record"] == first["value"]["record"]
    assert other_key["value"]["pushed"] is False
    assert default_head(verified_run) == head
    assert len(records_of(verified_run.store, "merge.completed")) == 1
    history = git_text(verified_run.project, "log", "--oneline")
    assert sum(
        1 for line in history.splitlines() if line.startswith(first["value"]["commit"][:7])
    ) == 1


def test_merge_refuses_an_unknown_run(
    verified_run: SimpleNamespace, fake_droid: Any
) -> None:
    launched = len(fake_droid.log())

    r = invoke(
        "merge",
        {
            "item": verified_run.item,
            "run": "01JUNKNOWN000000000000000",
            "expect": verified_run.etag,
            "key": "m-unknown",
        },
        store=verified_run.store,
        actor="cto",
    )

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert len(fake_droid.log()) == launched
    assert default_head(verified_run) == verified_run.base


def test_merge_is_locked_out_before_approval(unapproved_store: Path) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=unapproved_store, actor="cto"
    )
    assert added["ok"], added

    r = invoke(
        "merge",
        {
            "item": "login-page",
            "run": "01JRUN000000000000000001",
            "expect": "0123456789abcdef",
            "key": "m-lock",
        },
        store=unapproved_store,
        actor="cto",
    )

    assert r["ok"] is False, r
    assert r["error"]["code"] == "INVALID_STATE"
    assert r["error"]["details"]["reason"] == "approval_missing"
