"""Shared helpers for multi-repo portfolio and store-check tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from helpers import approve_picture, as_human, run_git
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.model.policy import DECISION_RIGHTS, HUMAN_ONLY_IDS
from context_rail.model.starter import starter_roles
from context_rail.store.doc import etag


def _answers_file(root: Path, pack: str) -> Path:
    path = Path(root) / f"answers-{pack}.json"
    payload = {
        "schema": 1,
        "pack": pack,
        "goal": {"title": "Ship project", "body": "Deliver the planned milestones."},
        "vision": {"title": "Reliable delivery", "body": "Everything passes checks."},
        "scope": [{"title": "Core", "body": "Core deliverables.", "priority": "must"}],
        "wont": [{"title": "Unrelated", "reason": "Out of scope."}],
        "charter": {
            "schema": 1,
            "project": {
                "goal": "ship-project",
                "vision": "reliable-delivery",
                "scope": ["core"],
                "wont": ["wont"],
            },
            "protected_files": [
                "rail.yaml",
                "charter.md",
                "project/ship-project.md",
                "project/reliable-delivery.md",
                "project/wont.md",
                "project/core.md",
            ],
            "gates": {
                name: {
                    "require_checks": False,
                    "require_acceptance": False,
                    "require_review": False,
                    "require_human": False,
                }
                for name in ("item_done", "scope_done", "merge", "release")
            },
            "human_in_loop": list(HUMAN_ONLY_IDS),
            "budgets": {
                "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
                "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
            },
            "thresholds": {
                "stuck_hours": 24,
                "near_budget_ratio": 0.8,
                "triage_reports": 2,
                "triage_weight": 2,
            },
            "change_rule": {"approval_required": True, "delegable": False},
            "decision_rights": {name: list(ids) for name, ids in DECISION_RIGHTS.items()},
            "default_branch": "main",
            "remote": "origin",
            "allow_push": False,
            "reviewer_different_model": False,
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def commit_all(root: Path, message: str) -> str:
    """Stage every file under ``root``, commit with ``message``, and return HEAD."""
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", message)
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def make_child_repo(
    root: Path,
    name: str,
    pack: str = "basic",
    *,
    roles: bool = False,
) -> SimpleNamespace:
    """Create an approved git-backed child store with one ``Token V2`` item."""
    child_root = Path(root) / name
    child_root.mkdir(parents=True, exist_ok=True)
    answers = _answers_file(child_root, pack)
    created = invoke(
        "init",
        {
            "dir": str(child_root),
            "pack": pack,
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    answers.unlink()
    assert is_ok(created), created
    store = Path(created["value"]["store"])

    if roles:
        local = store / "packs" / pack
        if not local.exists():
            copied = invoke(
                "pack create",
                {"name": f"{pack}-roles", "from": pack, "roles": True},
                store=store,
                actor="human",
            )
            assert is_ok(copied), copied
            pack_name = f"{pack}-roles"
            rail_bytes = (store / "rail.yaml").read_bytes()
            with as_human():
                used = invoke(
                    "pack use",
                    {
                        "name": pack_name,
                        "expect": etag(rail_bytes),
                        "confirm": f"USE PACK {pack_name}",
                    },
                    store=store,
                    actor="human",
                )
            assert is_ok(used), used
            with as_human():
                approved = approve_picture(store, actor=None)
                assert is_ok(approved), approved
        else:
            document = yaml.safe_load((local / "pack.yaml").read_text(encoding="utf-8"))
            document["operating"] = {
                **(document.get("operating") or {}),
                "version": 2,
                "roles": starter_roles(load_pack(pack, store)),
            }
            (local / "pack.yaml").write_text(
                yaml.safe_dump(document, sort_keys=False),
                encoding="utf-8",
                newline="\n",
            )
            with as_human():
                approved = approve_picture(store, actor=None)
                assert is_ok(approved), approved
    else:
        approved = approve_picture(store, actor="human")
        assert is_ok(approved), approved

    if roles:
        with as_human():
            added = invoke(
                "add",
                {"preset": "task", "title": "Token V2"},
                store=store,
            )
    else:
        added = invoke(
            "add",
            {"preset": "task", "title": "Token V2"},
            store=store,
            actor="cto",
        )
    assert is_ok(added), added
    item = added["value"]["slug"]

    run_git(child_root, "init", "-b", "main")
    commit_all(child_root, "init child")
    return SimpleNamespace(root=child_root, store=store, item=item)


def make_master_repo(root: Path) -> SimpleNamespace:
    """Create an approved git-backed master ``basic`` store with ``slice-a``."""
    master_root = Path(root) / "master"
    master_root.mkdir(parents=True, exist_ok=True)
    answers = _answers_file(master_root, "basic")
    created = invoke(
        "init",
        {
            "dir": str(master_root),
            "pack": "basic",
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    answers.unlink()
    assert is_ok(created), created
    store = Path(created["value"]["store"])
    approved = approve_picture(store, actor="human")
    assert is_ok(approved), approved
    added = invoke(
        "add",
        {"preset": "task", "title": "Slice A"},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    slice_slug = added["value"]["slug"]
    run_git(master_root, "init", "-b", "main")
    commit_all(master_root, "init master")
    return SimpleNamespace(root=master_root, store=store, slice=slice_slug)
