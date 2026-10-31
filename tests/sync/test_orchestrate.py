from __future__ import annotations

from context_rail.gateway.gateway import invoke
from helpers import init_store, require_git_identity


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


def test_doctor_counts_are_complete_beyond_the_issue_cap(tmp_path, monkeypatch) -> None:
    from context_rail.gateway.handlers import sync as sync_module
    import context_rail.doctor.checks as doctor

    store = init_store(tmp_path)

    def report(store, manifest, pack, index):
        return {
            "issues": [{"severity": "info"}],
            "counts": {"error": 101, "warning": 2, "info": 3},
        }

    monkeypatch.setattr(doctor, "run_doctor", report)

    assert sync_module._doctor_counts(store) == {"error": 101, "warning": 2, "info": 3}


def test_a_candidate_with_a_mismatched_approval_never_publishes(two_clones) -> None:
    require_git_identity()
    from helpers import git

    charter = two_clones.store_a / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nAn unapproved change.\n",
        encoding="utf-8",
        newline="\n",
    )
    git(two_clones.a, "add", "-A")
    git(two_clones.a, "commit", "-m", "unapproved charter change")
    git(two_clones.a, "push", "origin", "main")
    remote_before = git(two_clones.bare, "rev-parse", "main").strip()
    local_before = git(two_clones.b, "rev-parse", "HEAD").strip()

    refused = invoke("sync", {}, store=two_clones.store_b, actor="human")

    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "INVALID_STATE", refused
    assert refused["error"]["details"]["reason"] == "approval_mismatch"
    assert git(two_clones.b, "rev-parse", "HEAD").strip() == local_before
    assert git(two_clones.bare, "rev-parse", "main").strip() == remote_before


def test_a_candidate_with_a_matching_approval_still_publishes(two_clones) -> None:
    require_git_identity()
    from helpers import git

    added = invoke(
        "add",
        {"preset": "task", "title": "Approved change"},
        store=two_clones.store_a,
        actor="cto",
    )
    assert added["ok"] is True, added
    git(two_clones.a, "add", "-A")
    git(two_clones.a, "commit", "-m", "approved store change")
    git(two_clones.a, "push", "origin", "main")

    synced = invoke("sync", {}, store=two_clones.store_b, actor="human")

    assert synced["ok"] is True, synced
    assert (two_clones.store_b / "items" / "task" / "approved-change.md").is_file()


def test_the_candidate_manifest_is_validated_before_any_commit(tmp_path) -> None:
    from context_rail.gateway.handlers import sync as sync_module
    from context_rail.store.write import WriteRefused

    for candidate in ({}, {"rail.yaml": b"not: [valid"}):
        try:
            sync_module._validate_candidate(candidate, tmp_path)
        except WriteRefused as refused:
            assert refused.code == "VALIDATION_FAILED"
        else:
            raise AssertionError("an invalid candidate was accepted")


def test_a_post_publish_doctor_error_stops_the_push(two_clones, monkeypatch) -> None:
    require_git_identity()
    from context_rail.gateway.handlers import sync as sync_module
    from helpers import git

    pushed = []
    monkeypatch.setattr(
        sync_module, "_doctor_counts", lambda store: {"error": 1, "warning": 0, "info": 0}
    )
    monkeypatch.setattr(
        sync_module.git_sync, "push", lambda *a, **k: pushed.append(a)
    )
    added = invoke(
        "add", {"preset": "task", "title": "Hook edit"}, store=two_clones.store_a, actor="cto"
    )
    assert added["ok"] is True, added
    remote_before = git(two_clones.bare, "rev-parse", "main").strip()

    refused = invoke("sync", {}, store=two_clones.store_a, actor="human")

    assert refused["error"]["code"] == "INTEGRATION_FAILURE", refused
    assert refused["error"]["details"]["doctor"]["error"] == 1
    assert pushed == []
    assert git(two_clones.bare, "rev-parse", "main").strip() == remote_before


def _rooted_repo(tmp_path):
    from context_rail.core.result import is_ok
    from helpers import run_git

    root = tmp_path / "rooted"
    root.mkdir()
    created = invoke(
        "init", {"dir": str(root), "root": ".", "pack": "small"}, actor="human"
    )
    assert is_ok(created), created
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    run_git(root, "init")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "rooted store")
    return root


def test_a_root_store_owns_its_layout_not_the_checkout(tmp_path) -> None:
    from context_rail.model.manifest import load_manifest
    from context_rail.store.location import owns_path, store_prefixes

    root = _rooted_repo(tmp_path)
    prefixes = store_prefixes(root, root, load_manifest(root))

    assert "src" not in prefixes
    assert owns_path("items/task/a.md", prefixes) is True
    assert owns_path("rail.yaml", prefixes) is True
    assert owns_path("src/app.py", prefixes) is False


def test_a_dirty_source_file_in_a_root_store_is_outside(tmp_path) -> None:
    from context_rail.model.manifest import load_manifest
    from context_rail.runner import git_sync
    from context_rail.store.location import store_prefixes

    root = _rooted_repo(tmp_path)
    (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    prefixes = store_prefixes(root, root, load_manifest(root))

    dirty = git_sync.dirty_outside(root, root, ".", prefixes=prefixes)

    assert dirty == "src/app.py"


def test_dirty_outside_and_incoming_changes_use_the_same_paths(tmp_path) -> None:
    from context_rail.model.manifest import load_manifest
    from context_rail.store.location import owns_path, store_prefixes

    root = _rooted_repo(tmp_path)
    prefixes = store_prefixes(root, root, load_manifest(root))

    for path in (
        "src/app.py",
        "README.md",
        "items/task/a.md",
        "records/2026-10/01J.json",
        "rail.yaml",
    ):
        assert owns_path(path, prefixes) is (path not in {"src/app.py", "README.md"})
