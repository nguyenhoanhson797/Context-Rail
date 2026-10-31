from __future__ import annotations

from context_rail.gateway.gateway import invoke
from context_rail.store.sync import load_intent
from helpers import git, require_git_identity


def test_crash_after_publication_resumes_without_reset(two_clones, inject_fault) -> None:
    require_git_identity()
    invoke("add", {"preset": "task", "title": "Crash"}, store=two_clones.store_a, actor="cto")
    inject_fault("context_rail.gateway.handlers.sync", "_bookkeeping", mode="raise")
    invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert load_intent(two_clones.store_a)["phase"] in {"published", "committed"}
    resumed = invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert resumed["ok"] is True
    reflog = git(two_clones.a, "reflog", "--format=%gs")
    assert "reset" not in reflog and "checkout" not in reflog


def test_changed_head_refuses_the_advance_and_keeps_the_candidate(two_clones, inject_fault) -> None:
    require_git_identity()
    invoke("add", {"preset": "task", "title": "Moved"}, store=two_clones.store_a, actor="cto")
    inject_fault("context_rail.gateway.handlers.sync", "_head_now", mode="return", value="0" * 40)
    result = invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert result["error"]["code"] == "CONFLICT" and result["error"]["details"]["phase"] == "advance"
    assert git(two_clones.a, "rev-parse", "--verify", "refs/rail/sync/" + load_intent(two_clones.store_a)["key"]).strip()


def test_rejected_push_reports_conflict_and_a_rerun_integrates(two_clones) -> None:
    require_git_identity()
    invoke("add", {"preset": "task", "title": "First"}, store=two_clones.store_a, actor="cto")
    invoke("add", {"preset": "task", "title": "Second"}, store=two_clones.store_b, actor="cto")
    assert invoke("sync", {}, store=two_clones.store_b, actor="human")["ok"] is True
    rejected = invoke("sync", {}, store=two_clones.store_a, actor="human")
    if rejected["ok"] is False:
        assert rejected["error"]["code"] == "CONFLICT" and rejected["error"]["details"]["phase"] == "push"
    rerun = invoke("sync", {}, store=two_clones.store_a, actor="human")
    assert rerun["ok"] is True and rerun["value"]["pushed"] is True
