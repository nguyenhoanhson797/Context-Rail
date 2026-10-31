from __future__ import annotations

from context_rail.gateway.gateway import invoke
from helpers import require_git_identity


def test_disjoint_changes_converge_after_sync(two_clones) -> None:
    require_git_identity()
    invoke("add", {"preset": "task", "title": "Alpha"}, store=two_clones.store_a, actor="cto")
    invoke("add", {"preset": "task", "title": "Beta"}, store=two_clones.store_b, actor="cto")
    assert invoke("sync", {}, store=two_clones.store_a, actor="human")["ok"] is True
    assert invoke("sync", {}, store=two_clones.store_b, actor="human")["ok"] is True
    assert invoke("sync", {}, store=two_clones.store_a, actor="human")["ok"] is True
    assert invoke("reindex", {"full": True}, store=two_clones.store_a, actor="cto")["ok"] is True
    assert invoke("reindex", {"full": True}, store=two_clones.store_b, actor="cto")["ok"] is True
    a = invoke("find", {"preset": "task"}, store=two_clones.store_a, actor="cto")["value"]["items"]
    b = invoke("find", {"preset": "task"}, store=two_clones.store_b, actor="cto")["value"]["items"]
    assert a == b and {row["title"] for row in a} == {"Alpha", "Beta"}


def test_status_conflict_stops_sync_and_pushes_nothing(two_clones) -> None:
    require_git_identity()
    slug = invoke("add", {"preset": "task", "title": "Shared"}, store=two_clones.store_a, actor="cto")["value"]["slug"]
    invoke("sync", {}, store=two_clones.store_a, actor="human")
    invoke("sync", {}, store=two_clones.store_b, actor="human")
    ea = invoke("show", {"slug": slug}, store=two_clones.store_a, actor="cto")["value"]["doc"]["etag"]
    eb = invoke("show", {"slug": slug}, store=two_clones.store_b, actor="cto")["value"]["doc"]["etag"]
    invoke("move", {"slug": slug, "status": "doing", "expect": ea}, store=two_clones.store_a, actor="cto")
    moved = invoke("move", {"slug": slug, "status": "doing", "expect": eb}, store=two_clones.store_b, actor="cto")
    assert moved["ok"] is True, moved
    blocked_etag = invoke("show", {"slug": slug}, store=two_clones.store_b, actor="cto")["value"]["doc"]["etag"]
    invoke("move", {"slug": slug, "status": "blocked", "expect": blocked_etag}, store=two_clones.store_b, actor="cto")
    invoke("sync", {}, store=two_clones.store_a, actor="human")
    refused = invoke("sync", {}, store=two_clones.store_b, actor="human")
    assert refused["error"]["code"] == "CONFLICT"
    assert "status" in str(refused["error"]["details"])


def test_non_store_commit_on_the_branch_is_refused(two_clones) -> None:
    require_git_identity()
    (two_clones.a / "code.py").write_text("print(1)\n", encoding="utf-8")
    from helpers import git
    git(two_clones.a, "add", "--", "code.py")
    git(two_clones.a, "commit", "-m", "foreign code")
    git(two_clones.a, "push", "origin", "main")
    result = invoke("sync", {}, store=two_clones.store_b, actor="human")
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_detached_head_and_staged_foreign_changes_refuse(two_clones) -> None:
    from helpers import git
    (two_clones.a / "outside.txt").write_text("x", encoding="utf-8")
    git(two_clones.a, "add", "--", "outside.txt")
    assert invoke("sync", {}, store=two_clones.store_a, actor="human")["error"]["code"] == "INVALID_STATE"


def test_fetch_failure_is_an_infrastructure_error_never_success(two_clones) -> None:
    gone = two_clones.bare.with_name("remote-gone.git")
    two_clones.bare.rename(gone)
    result = invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert result["error"]["code"] == "INTEGRATION_FAILURE"
    assert result["error"]["details"]["phase"] == "fetch"


def test_option_like_remote_is_refused_before_the_runner(two_clones, monkeypatch) -> None:
    import context_rail.runner.git_sync as git_sync

    called = []
    monkeypatch.setattr(git_sync, "fetch", lambda *a, **k: called.append(a))
    result = invoke("sync", {"remote": "--upload-pack=evil"}, store=two_clones.store_a, actor="human")
    assert result["error"]["code"] == "VALIDATION_FAILED" and called == []


def test_missing_identity_is_an_explicit_failure_and_commits_nothing(two_clones, inject_fault) -> None:
    inject_fault("context_rail.runner.git_sync", "identity_ok", mode="return", value=False)
    result = invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert result["error"]["code"] == "INTEGRATION_FAILURE"
    assert result["error"]["process"] == "git"
