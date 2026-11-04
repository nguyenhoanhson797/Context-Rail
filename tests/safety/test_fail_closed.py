"""Fail-closed refusals: unknown models are refused and paths cannot escape."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import TOOLS
from helpers import run_cli

_REFUSED_TOOLS = (
    "approve",
    "add",
    "update",
    "move",
    "link",
    "find",
    "show",
    "types",
    "next",
    "model",
    "explain",
    "why",
    "status",
    "reindex",
    "rename",
    "relocate",
    "doctor",
    "check",
    "verify",
    "dispatch",
    "review",
    "merge",
    "do",
    "report",
    "triage",
    "cto brief",
    "cto tick",
    "ledger",
    "digest",
    "adapters sync",
    "ci",
    "pack list",
    "pack show",
    "pack validate",
    "pack create",
    "pack use",
    "measure versions",
    "measure fix",
    "rollback",
    "sync",
    "sync driver",
    "mcp serve",
    "ui serve",
    "serve daemon",
)
_ARGS: dict[str, dict[str, Any]] = {
    "approve": {"target": "charter", "confirm": "APPROVE charter", "confirmer": "v"},
    "add": {"preset": "task", "title": "Refused"},
    "update": {"slug": "t0", "expect": "0123456789abcdef", "set": ["estimate=1"]},
    "move": {"slug": "t0", "status": "doing", "expect": "0123456789abcdef"},
    "link": {
        "src": "t0",
        "rel": "part_of",
        "dst": "g0",
        "expect": "0123456789abcdef",
        "reason": "planning",
    },
    "find": {"preset": "task"},
    "show": {"slug": "t0"},
    "types": {},
    "next": {},
    "model": {},
    "explain": {"name": "task"},
    "why": {"slug": "t0"},
    "status": {},
    "reindex": {},
    "rename": {"old": "t0", "new": "renamed"},
    "relocate": {},
    "doctor": {},
    "check": {"slug": "smoke", "spec_file": "missing.json", "expect": "absent"},
    "verify": {"target": "smoke"},
    "dispatch": {"item": "t0", "expect": "0123456789abcdef", "key": "k"},
    "review": {"item": "t0", "run": "01JRUN000000000000000001", "key": "k"},
    "merge": {"item": "t0", "run": "01JRUN000000000000000001", "expect": "0123456789abcdef", "key": "k"},
    "do": {"action": "start", "item": "t0"},
    "report": {
        "item": "t0",
        "run": "01JRUN000000000000000001",
        "report_file": "missing.json",
    },
    "triage": {},
    "cto brief": {},
    "cto tick": {"key": "k"},
    "ledger": {},
    "digest": {},
    "adapters sync": {},
    "ci": {},
    "pack list": {},
    "pack show": {"name": "small"},
    "pack validate": {"name": "small"},
    "pack create": {"name": "draft", "from": "small"},
    "pack use": {"name": "indie", "expect": "0" * 16, "confirm": "USE PACK indie"},
    "measure versions": {"target_class": "skill", "target": "test-first", "before": 1, "after": 2},
    "measure fix": {"item": "t0"},
    "rollback": {"target_class": "skill", "target": "workspace/skills/test-first.md", "version": 1, "expect": "0" * 16},
    "sync": {},
    "sync driver": {"kind": "doc", "base": "b", "ours": "o", "theirs": "t", "path": "p"},
    "mcp serve": {},
    "ui serve": {},
    "serve daemon": {},
}
_ZONK = f"""---
primitive: item
preset: task
title: Outside Task
status: todo
tags: [task]
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
---
# Outside Task

Only this outside body mentions zonk.
"""


def _manifest_mapping(store: Path) -> dict[str, Any]:
    return yaml.safe_load((store / "rail.yaml").read_text(encoding="utf-8"))


def _write_manifest(store: Path, mapping: dict[str, Any]) -> None:
    (store / "rail.yaml").write_text(
        yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8"
    )


def _files(store: Path) -> dict[str, str]:
    """Every authoritative store file with its digest; the cache is excluded."""
    return {
        path.relative_to(store).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(store.rglob("*"))
        if path.is_file() and ".rail-local" not in path.relative_to(store).parts
    }


def _builtin_pack() -> dict[str, Any]:
    text = (
        resources.files("context_rail")
        .joinpath("packs", "basic", "pack.yaml")
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(text)


def _write_pack(store: Path, mapping: dict[str, Any]) -> None:
    path = store / "packs" / "basic" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")


def _assert_tools_refuse(store: Path, codes: set[str]) -> None:
    before = _files(store)
    for name in ("status", "doctor"):
        result = invoke(name, {}, store=store)
        assert result["ok"] is False, (name, result)
        assert result["error"]["code"] in codes, (name, result)
        proc = run_cli(store.parent, name, store=store)
        assert proc.returncode == 1, (name, proc.stderr)
        assert json.loads(proc.stdout)["error"]["code"] in codes, (name, proc.stdout)
    assert _files(store) == before


def test_schema_four_refuses_every_tool_except_init(store_factory: Any) -> None:
    store = store_factory(docs=20)
    manifest = _manifest_mapping(store)
    manifest["schema"] = 4
    _write_manifest(store, manifest)
    before = _files(store)

    assert set(_REFUSED_TOOLS) == {tool.name for tool in TOOLS} - {"init", "migrate"}
    for name in _REFUSED_TOOLS:
        result = invoke(name, _ARGS[name], store=store, actor="cto")
        assert result["ok"] is False, (name, result)
        assert result["error"]["code"] == "SCHEMA_UNSUPPORTED", (name, result)

    assert _files(store) == before
    refused = run_cli(store.parent, "show", "t0", store=store)
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["error"]["code"] == "SCHEMA_UNSUPPORTED"


def test_unknown_manifest_key_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    manifest = _manifest_mapping(store)
    manifest["foo"] = 1
    _write_manifest(store, manifest)
    _assert_tools_refuse(store, {"VALIDATION_FAILED"})


def test_unknown_pack_key_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    pack = _builtin_pack()
    pack["color"] = "red"
    _write_pack(store, pack)
    _assert_tools_refuse(store, {"VALIDATION_FAILED"})


def test_unknown_policy_key_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    pack = _builtin_pack()
    pack["policy"] = {**pack["policy"], "foo": 1}
    _write_pack(store, pack)
    _assert_tools_refuse(store, {"VALIDATION_FAILED"})


def test_unknown_pack_version_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    pack = _builtin_pack()
    pack["version"] = 3
    _write_pack(store, pack)
    _assert_tools_refuse(store, {"SCHEMA_UNSUPPORTED", "VALIDATION_FAILED"})


def test_set_path_escape_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    before = _files(store)
    proc = run_cli(
        store.parent,
        "add",
        "task",
        "Escape",
        "--set",
        "path=../../escape",
        store=store,
    )
    assert proc.returncode == 1, proc.stderr
    assert json.loads(proc.stdout)["error"]["code"] == "VALIDATION_FAILED"
    assert _files(store) == before
    assert not (store.parent / "escape").exists()
    assert not (store.parent.parent / "escape").exists()


def test_rename_escape_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=20)
    before = _files(store)
    proc = run_cli(store.parent, "rename", "t0", "../escape", store=store)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(proc.stdout)["error"]["code"] in {
        "VALIDATION_FAILED",
        "FORBIDDEN",
    }
    assert _files(store) == before
    assert not (store.parent / "escape").exists()


def test_init_root_escape_is_forbidden(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    proc = run_cli(project, "init", "--dir", str(project), "--root", "../escape")
    assert proc.returncode == 1, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False and payload["error"]["code"] == "FORBIDDEN"
    assert not (tmp_path / "escape").exists()


def test_body_file_outside_the_store_is_read_only(
    store_factory: Any, tmp_path: Path
) -> None:
    store = store_factory(docs=20)
    outside = tmp_path / "outside.md"
    outside.write_text("External body\n", encoding="utf-8")
    before = outside.read_bytes()

    proc = run_cli(
        store.parent,
        "add",
        "note",
        "Ext",
        "--body-file",
        str(outside),
        store=store,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    shown = run_cli(store.parent, "show", "ext", store=store)
    assert shown.returncode == 0, shown.stderr
    assert "External body" in json.loads(shown.stdout)["value"]["body"]
    assert outside.read_bytes() == before


def test_symlinks_cannot_escape_the_store(
    store_factory: Any, tmp_path: Path
) -> None:
    store = store_factory(docs=10)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "zonk.md").write_text(_ZONK, encoding="utf-8")
    try:
        (store / "items" / "task" / "linked").symlink_to(outside, target_is_directory=True)
        (store / "items" / "task" / "linked.md").symlink_to(outside / "zonk.md")
    except OSError as exc:
        pytest.skip(f"symlinks are not permitted here: {exc}")

    outside_dir = invoke("find", {"text": "zonk"}, store=store)
    assert outside_dir["ok"] and outside_dir["value"]["items"] == []
    listed = invoke("find", {"preset": "task", "limit": 100}, store=store)
    assert listed["ok"]
    assert all(
        not item["path"].startswith("items/task/linked") for item in listed["value"]["items"]
    )
