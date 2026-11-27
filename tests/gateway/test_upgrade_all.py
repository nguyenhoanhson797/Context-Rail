"""`rail ops upgrade --all` plans every store, then upgrades the safe ones."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from helpers import run_cli, tree_bytes, write_legacy_runtime_files

from context_rail.core.result import is_ok
from context_rail.gateway.upgrade_all import invoke_all


def _copy_store(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".rail-local"))
    return target


def test_upgrade_all_dry_run_lists_stores_and_skips_build_trees(
    approved_store: Path, tmp_path: Path
) -> None:
    root = tmp_path / "workspace"
    _copy_store(approved_store, root / "alpha" / "context-rail")
    _copy_store(approved_store, root / "beta" / "context-rail")
    _copy_store(approved_store, root / "dist" / "context-rail")

    result = invoke_all({"dry_run": True, "root": str(root)}, store=None, actor="human")

    assert is_ok(result), result
    rows = result["value"]["stores"]
    assert [row["path"] for row in rows] == ["alpha/context-rail", "beta/context-rail"]
    assert all(row["status"] != "needs a human" for row in rows)
    assert "dist" not in result["value"]["text"]
    assert "Store" in result["value"]["text"]


def test_upgrade_all_leaves_a_store_that_needs_a_human_untouched(
    approved_store: Path, tmp_path: Path
) -> None:
    root = tmp_path / "workspace"
    _copy_store(approved_store, root / "safe" / "context-rail")
    blocked = _copy_store(approved_store, root / "blocked" / "context-rail")
    write_legacy_runtime_files(blocked)
    before = tree_bytes(blocked)

    result = invoke_all(
        {"root": str(root), "confirm": "UPGRADE store"},
        store=None,
        actor="human",
    )

    assert is_ok(result), result
    rows = {row["path"]: row for row in result["value"]["stores"]}
    assert rows["blocked/context-rail"]["status"] == "needs a human"
    assert "rail approve charter" in rows["blocked/context-rail"]["next"]
    assert tree_bytes(blocked) == before
    assert not (root / "blocked" / "AGENTS.md").exists()

    assert rows["safe/context-rail"]["status"] != "needs a human"
    assert (root / "safe" / "AGENTS.md").is_file()


def test_upgrade_all_cli_prints_the_table_beside_json(
    approved_store: Path, tmp_path: Path
) -> None:
    root = tmp_path / "workspace"
    _copy_store(approved_store, root / "alpha" / "context-rail")

    proc = run_cli(
        root,
        "--actor",
        "human",
        "ops",
        "upgrade",
        "--all",
        "--dry-run",
        "--root",
        str(root),
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["value"]["all"] is True
    assert "alpha/context-rail" in proc.stderr
    assert "Store" in proc.stderr
