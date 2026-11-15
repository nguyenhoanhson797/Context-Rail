"""Verification and file-only freshness of ``store`` checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import (
    approve_picture,
    as_human,
    item_etag,
    latest_record,
    refusal,
    require_git_identity,
    run_git,
    touch_doc,
    tree_bytes,
)
from portfolio_helpers import _answers_file, commit_all, make_child_repo, make_master_repo

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.runner import git as git_runner
from context_rail.store.doc import etag


def _spec_file(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _store_spec(
    store_rel: str,
    *,
    item: str = "token-v2",
    category: str = "done",
    verifies: list[str] | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schema": 1,
        "mode": "store",
        "acceptance": False,
        "store": store_rel,
        "item": item,
        "category": category,
    }
    if verifies is not None:
        spec["verifies"] = verifies
    return spec


def _declare_store_check(
    master_store: Path,
    tmp_path: Path,
    slug: str,
    store_rel: str,
    *,
    item: str = "token-v2",
    category: str = "done",
    target: str = "slice-a",
) -> dict[str, Any]:
    spec = _spec_file(
        tmp_path,
        f"{slug}.json",
        _store_spec(store_rel, item=item, category=category),
    )
    return invoke(
        "check",
        {
            "slug": slug,
            "target": target,
            "spec_file": str(spec),
            "expect": "absent",
        },
        store=master_store,
        actor="cto",
    )


def _move_child_item(child: Any, status: str) -> None:
    if status == "done":
        current_etag = item_etag(child)
        moved = invoke(
            "move",
            {"slug": child.item, "status": "doing", "expect": current_etag},
            store=child.store,
            actor="cto",
        )
        assert is_ok(moved), moved
    current_etag = item_etag(child)
    moved = invoke(
        "move",
        {"slug": child.item, "status": status, "expect": current_etag},
        store=child.store,
        actor="cto",
    )
    assert is_ok(moved), moved


def test_declare_refuses_master_outside_git(tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    answers = _answers_file(plain_dir, "basic")
    created = invoke(
        "init",
        {
            "dir": str(plain_dir),
            "pack": "basic",
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    answers.unlink()
    assert is_ok(created), created
    store = Path(created["value"]["store"])
    assert is_ok(approve_picture(store, actor="human"))
    assert is_ok(invoke("add", {"preset": "task", "title": "Slice A"}, store=store, actor="cto"))
    make_child_repo(tmp_path, "auth")

    result = _declare_store_check(store, tmp_path, "auth-proof", "../auth/context-rail")

    assert refusal(result) == ("VALIDATION_FAILED", "store_not_in_git")
    assert not (store / "checks" / "auth-proof.md").exists()


def test_declare_refuses_a_path_outside_the_bound(tmp_path: Path) -> None:
    require_git_identity()
    workspace = tmp_path / "workspace"
    master = make_master_repo(workspace)
    outside = make_child_repo(tmp_path, "elsewhere")

    result = _declare_store_check(
        master.store,
        tmp_path,
        "outside-proof",
        "../../elsewhere/context-rail",
        target=master.slice,
    )

    assert refusal(result) == ("VALIDATION_FAILED", "store_outside_bound")
    assert outside.store.is_dir()


def test_declare_refuses_the_master_itself(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)

    result = _declare_store_check(
        master.store,
        tmp_path,
        "self-proof",
        "context-rail",
        target=master.slice,
    )

    assert refusal(result) == ("VALIDATION_FAILED", "store_is_self")


def test_declare_refuses_a_folder_without_rail_yaml(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    empty_dir = tmp_path / "auth" / "context-rail"
    empty_dir.mkdir(parents=True)

    result = _declare_store_check(
        master.store,
        tmp_path,
        "missing-store-proof",
        "../auth/context-rail",
        target=master.slice,
    )

    assert refusal(result) == ("VALIDATION_FAILED", "store_missing")


def test_backslash_and_trailing_slash_resolve_alike(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    declared = _declare_store_check(
        master.store,
        tmp_path,
        "win-slash-proof",
        "..\\auth\\context-rail\\",
        target=master.slice,
    )

    assert is_ok(declared), declared
    verified = invoke("verify", {"target": "win-slash-proof"}, store=master.store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    assert verified["value"]["verdicts"][0]["freshness"] == "fresh"


def test_verify_passes_for_a_committed_done_item_in_a_sibling_repo(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    child_head = commit_all(child.root, "complete token-v2")

    declared = _declare_store_check(
        master.store,
        tmp_path,
        "auth-token-done",
        "../auth/context-rail",
        target=master.slice,
    )
    assert is_ok(declared), declared

    verified = invoke("verify", {"target": "auth-token-done"}, store=master.store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    entry = verified["value"]["verdicts"][0]
    assert entry["freshness"] == "fresh"

    verdict = latest_record(master.store, "check.verdict")
    item_rel = "items/task/token-v2.md"
    item_bytes = (child.store / item_rel).read_bytes()
    assert verdict["data"]["store"] == {
        "path": "../auth/context-rail",
        "item": "token-v2",
        "doc": item_rel,
        "commit": child_head,
        "etag": etag(item_bytes),
        "category": "done",
    }
    state = invoke("check list", {}, store=master.store)
    assert is_ok(state), state


def test_verify_passes_for_a_submodule_child(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth-upstream")
    _move_child_item(child, "done")
    child_head = commit_all(child.root, "complete token-v2")

    proc = subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(child.root),
            "projects/auth",
        ],
        cwd=master.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    commit_all(master.root, "add auth submodule")

    declared = _declare_store_check(
        master.store,
        tmp_path,
        "sub-proof",
        "projects/auth/context-rail",
        target=master.slice,
    )
    assert is_ok(declared), declared

    verified = invoke("verify", {"target": "sub-proof"}, store=master.store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    assert verified["value"]["verdicts"][0]["freshness"] == "fresh"
    verdict = latest_record(master.store, "check.verdict")
    assert verdict["data"]["store"]["commit"] == child_head


def test_verify_fails_when_the_category_differs(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "doing")
    commit_all(child.root, "start token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert "active" in (verified["value"]["reason"] or "")


def test_verify_fails_for_an_uncommitted_item(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert "uncommitted" in (verified["value"]["reason"] or "").lower()


def test_verify_fails_for_an_untracked_item(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    added = invoke("add", {"preset": "task", "title": "Untracked Task"}, store=child.store, actor="cto")
    assert is_ok(added), added

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            item=added["value"]["slug"],
            category="not_started",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert "untracked" in (verified["value"]["reason"] or "").lower()


def test_verify_fails_for_a_missing_item(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    make_child_repo(tmp_path, "auth")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            item="no-such-item",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert "no-such-item" in (verified["value"]["reason"] or "")


def test_verify_fails_for_a_duplicate_slug(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    dup_path = child.store / "items" / "duplicate" / "token-v2.md"
    dup_path.parent.mkdir(parents=True, exist_ok=True)
    dup_path.write_bytes((child.store / "items" / "task" / "token-v2.md").read_bytes())
    commit_all(child.root, "add duplicate slug")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert "ambiguous" in (verified["value"]["reason"] or "").lower()


def test_verify_fails_for_an_unknown_child_pack_version(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "complete token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )

    local_pack = child.store / "packs" / "future" / "pack.yaml"
    local_pack.parent.mkdir(parents=True, exist_ok=True)
    basic_yaml = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "src" / "context_rail" / "packs" / "basic" / "pack.yaml").read_text(
            encoding="utf-8"
        )
    )
    basic_yaml["name"] = "future"
    basic_yaml["version"] = 99
    local_pack.write_text(yaml.safe_dump(basic_yaml, sort_keys=False), encoding="utf-8", newline="\n")
    manifest = yaml.safe_load((child.store / "rail.yaml").read_text(encoding="utf-8"))
    manifest["pack"] = "future"
    (child.store / "rail.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n")

    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert verified["value"]["reason"]


def test_verify_ignores_the_child_role_reach(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth", roles=True)
    with as_human():
        e1 = invoke("show", {"slug": child.item}, store=child.store)["value"]["doc"]["etag"]
        moved1 = invoke(
            "move",
            {"slug": child.item, "status": "doing", "expect": e1},
            store=child.store,
        )
        assert is_ok(moved1), moved1
        e2 = invoke("show", {"slug": child.item}, store=child.store)["value"]["doc"]["etag"]
        moved2 = invoke(
            "move",
            {"slug": child.item, "status": "done", "expect": e2},
            store=child.store,
        )
        assert is_ok(moved2), moved2
    commit_all(child.root, "finish role child item")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-role-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-role-proof"}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    assert verified["value"]["verdicts"][0]["freshness"] == "fresh"


def test_item_wide_run_includes_store_checks(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": master.slice}, store=master.store, actor="cto")

    assert is_ok(verified), verified
    assert [entry["check"] for entry in verified["value"]["verdicts"]] == ["auth-proof"]
    assert verified["value"]["passed"] is True


def test_freshness_goes_stale_when_the_child_commit_moves(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    assert is_ok(invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto"))
    assert invoke("show", {"slug": "auth-proof"}, store=master.store)["value"]["freshness"] == "fresh"

    (child.root / "README.md").write_text("changed\n", encoding="utf-8", newline="\n")
    commit_all(child.root, "move head")

    assert invoke("show", {"slug": "auth-proof"}, store=master.store)["value"]["freshness"] == "stale"


def test_freshness_goes_stale_when_the_item_file_changes(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    assert is_ok(invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto"))
    assert invoke("show", {"slug": "auth-proof"}, store=master.store)["value"]["freshness"] == "fresh"

    touch_doc(child.store, "items/task/token-v2.md", title="Edited Token V2")

    assert invoke("show", {"slug": "auth-proof"}, store=master.store)["value"]["freshness"] == "stale"


def test_freshness_is_unknown_when_the_child_folder_is_gone(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    assert is_ok(invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto"))

    child.root.rename(tmp_path / "auth-renamed")

    assert invoke("show", {"slug": "auth-proof"}, store=master.store)["value"]["freshness"] == "unknown"


def test_freshness_reads_without_running_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    assert is_ok(invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto"))

    def forbid(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("freshness must not invoke git")

    for name in ("git", "ls_files", "ls_files_staged", "status_porcelain", "rev_parse_head"):
        monkeypatch.setattr(git_runner, name, forbid)

    shown = invoke("show", {"slug": "auth-proof"}, store=master.store)
    assert is_ok(shown), shown
    assert shown["value"]["freshness"] == "fresh"

    listed = invoke("show", {"slug": master.slice}, store=master.store)
    assert is_ok(listed), listed
    assert listed["value"]["checks"][0]["freshness"] == "fresh"


def test_store_checks_never_write_the_child_store(tmp_path: Path) -> None:
    require_git_identity()
    master = make_master_repo(tmp_path)
    child = make_child_repo(tmp_path, "auth")
    _move_child_item(child, "done")
    commit_all(child.root, "finish token-v2")

    before = tree_bytes(child.store)
    assert is_ok(
        _declare_store_check(
            master.store,
            tmp_path,
            "auth-proof",
            "../auth/context-rail",
            target=master.slice,
        )
    )
    verified = invoke("verify", {"target": "auth-proof"}, store=master.store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True

    assert tree_bytes(child.store) == before
