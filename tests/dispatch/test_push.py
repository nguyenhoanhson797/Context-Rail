"""Optional push: the charter gate, the fast-forward push and its failures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from helpers import (
    default_head,
    item_etag,
    item_status,
    latest_record,
    merge_call,
    records_of,
    remote_head,
    run_git,
)

HOOK = "#!/bin/sh\necho 'the remote rejects this push' >&2\nexit 1\n"


def git_text(where: Path, *args: str) -> str:
    return run_git(where, *args).stdout.rstrip("\r\n")


def merged_candidate(run: SimpleNamespace) -> str:
    return str(latest_record(run.store, "merge.completed")["data"]["candidate_commit"])


def reject_pushes(run: SimpleNamespace) -> Path:
    hook = Path(run.remote) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    return hook


def test_push_refused_without_allow_push(verified_run: SimpleNamespace) -> None:
    r = merge_call(verified_run, "p1", push=True)

    assert r["ok"] is False, r
    assert r["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert remote_head(verified_run) is None
    assert remote_head(verified_run) != default_head(verified_run)
    assert git_text(Path(verified_run.remote), "for-each-ref") == ""
    assert records_of(verified_run.store, "merge.completed") == []
    assert default_head(verified_run) == verified_run.base
    assert item_status(verified_run) != "done"


def test_push_with_allow_push_equals_remote_head(push_run: SimpleNamespace) -> None:
    before = remote_head(push_run)
    assert before == default_head(push_run)

    r = merge_call(push_run, "p2", push=True)

    assert r["ok"], r
    assert r["value"]["merged"] is True
    assert r["value"]["pushed"] is True
    assert r["value"]["closed"] == push_run.item
    assert remote_head(push_run) == default_head(push_run)
    run_git(Path(push_run.remote), "merge-base", "--is-ancestor", before, remote_head(push_run))
    assert git_text(Path(push_run.remote), "tag") == ""
    pushes = records_of(push_run.store, "push.result")
    assert len(pushes) == 1
    assert pushes[0]["data"]["pushed"] is True
    run_git(push_run.project, "merge-base", "--is-ancestor", merged_candidate(push_run), "HEAD")


def test_push_failure_preserves_the_merge_and_records_a_pending_push(
    push_run: SimpleNamespace,
) -> None:
    before = remote_head(push_run)
    reject_pushes(push_run)

    r = merge_call(push_run, "p3", push=True)

    assert r["ok"], r
    value = r["value"]
    assert value["merged"] is True
    assert value["pushed"] is False
    assert value["pending_push"] is not None
    assert value["pending_push"]["remote"] == "origin"
    assert value["pending_push"]["branch"] == "main"
    assert remote_head(push_run) == before
    assert default_head(push_run) == value["commit"]
    run_git(push_run.project, "merge-base", "--is-ancestor", merged_candidate(push_run), "HEAD")
    assert len(records_of(push_run.store, "merge.completed")) == 1
    pushes = records_of(push_run.store, "push.result")
    assert len(pushes) == 1
    assert pushes[0]["data"]["pushed"] is False
    assert pushes[0]["data"]["error"]


def test_replay_reconciles_the_observed_head_and_pushes(
    push_run: SimpleNamespace,
) -> None:
    hook = reject_pushes(push_run)
    first = merge_call(push_run, "k-m9", push=True)
    assert first["ok"], first
    assert first["value"]["merged"] is True
    assert first["value"]["pushed"] is False
    hook.unlink()

    push_run.etag = item_etag(push_run)
    second = merge_call(push_run, "k-m10", push=True)

    assert second["ok"], second
    assert second["value"]["pushed"] is True
    assert second["value"]["merged"] is True
    assert remote_head(push_run) == default_head(push_run)
    assert len(records_of(push_run.store, "merge.completed")) == 1
    history = git_text(push_run.project, "log", "--oneline")
    assert sum(
        1
        for line in history.splitlines()
        if line.startswith(merged_candidate(push_run)[:7])
    ) == 1
    assert item_status(push_run) == "done"
