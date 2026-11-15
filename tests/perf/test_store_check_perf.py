"""Performance budget for ``store`` check freshness over a 1,000-doc child store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import median_ms, require_git_identity, run_git
from perf.gen import approve_generated, make_store
from portfolio_helpers import commit_all, make_master_repo

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.gateway.handlers.checks import freshness_of
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store import query


@pytest.mark.perf
def test_store_check_freshness_under_50_ms_for_1000_doc_child(tmp_path: Path) -> None:
    require_git_identity()

    child_root = tmp_path / "child"
    child_store = make_store(child_root, 1000)
    approve_generated(child_store)
    added = invoke(
        "add",
        {"preset": "task", "title": "Target Done Item"},
        store=child_store,
        actor="cto",
    )
    assert is_ok(added), added
    target_item = added["value"]["slug"]
    for status in ("doing", "done"):
        shown = invoke("show", {"slug": target_item}, store=child_store, actor="cto")
        assert is_ok(shown), shown
        moved = invoke(
            "move",
            {"slug": target_item, "status": status, "expect": shown["value"]["doc"]["etag"]},
            store=child_store,
            actor="cto",
        )
        assert is_ok(moved), moved
    run_git(child_root, "init", "-b", "main")
    commit_all(child_root, "seed 1000-doc child store")

    master = make_master_repo(tmp_path)
    spec = tmp_path / "child-store-check.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "store",
                "acceptance": False,
                "store": "../child/context-rail",
                "item": target_item,
                "category": "done",
            }
        ),
        encoding="utf-8",
    )
    declared = invoke(
        "check",
        {
            "slug": "child-done-proof",
            "target": master.slice,
            "spec_file": str(spec),
            "expect": "absent",
        },
        store=master.store,
        actor="cto",
    )
    assert is_ok(declared), declared

    verified = invoke(
        "verify",
        {"target": "child-done-proof"},
        store=master.store,
        actor="cto",
    )
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True, verified

    ctx = Context(
        store=master.store,
        manifest=load_manifest(master.store),
        pack=load_pack(load_manifest(master.store).pack, master.store),
        actor="cto",
    )
    try:
        ctx.index.sync()
        row = query.check_state(ctx.index, "child-done-proof")
        assert row is not None
        assert freshness_of(ctx, row) == "fresh"

        reading = median_ms(lambda: freshness_of(ctx, row), 20)
        print(f"store check freshness_of @1000 child docs: {reading:.2f} ms")
        assert reading < 50.0, f"freshness_of took {reading:.2f} ms (budget 50.0 ms)"
    finally:
        ctx.close()
