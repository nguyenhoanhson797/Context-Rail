"""Stage 2 doctor findings: pack identity, record identity and sync state.

Every check reads the index the caller already synced, plus only those store
files the index cannot answer (the pack policy, the Record payloads, the sync
intent). No check runs git, and none repairs anything except the write intents
``run_doctor`` already rolls forward.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.doctor.gitconfig import static_registration
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest
from context_rail.model.pack import Pack, load_pack, pack_policy_digest
from context_rail.store import query
from context_rail.store.approval import protected_snapshot
from context_rail.store.index import Index
from context_rail.store.location import worktree_git_dir
from context_rail.store.sync import intent_path

__all__ = ["SEVERITY", "issues"]

SEVERITY: dict[str, str] = {
    "invalid_selected_pack": "error",
    # Stage 1a's ``preset_field_hint`` already reports out-of-enum document
    # values; this name stays reserved for the pack migration report.
    "unsupported_preset_value": "warning",
    "pack_policy_unapproved": "error",
    "missing_sync_driver": "warning",
    "duplicate_record_id": "error",
    "version_history_mismatch": "error",
    "interrupted_sync": "warning",
}

_SYNC_INSTALL = "rail ops sync install"
_VERSIONED_KINDS = ("skill.changed", "profile.changed")
_BUILTIN_PROBE = ".rail-builtin-probe"


def issues(store: Path, manifest: Manifest, pack: Pack, ix: Index) -> list[dict[str, Any]]:
    """Every Stage 2 finding this store has, each with its path and a fix."""
    store = Path(store)
    context = SimpleNamespace(store=store, manifest=manifest, pack=pack, index=ix)
    found: list[dict[str, Any]] = []
    found.extend(_selected_pack(store, manifest))
    found.extend(_pack_policy(context, store, pack, ix))
    found.extend(_drivers(store))
    found.extend(_records(ix))
    found.extend(_versions(store))
    found.extend(_sync_intent(store))
    return found


def _selected_pack(store: Path, manifest: Manifest) -> list[dict[str, Any]]:
    try:
        load_pack(manifest.pack, store)
    except ModelError as error:
        return [
            _issue(
                "invalid_selected_pack",
                "rail.yaml",
                f"the selected pack {manifest.pack!r} cannot be read: "
                f"{error.message}",
                fix=f"rail ops pack validate --name {manifest.pack}",
            )
        ]
    return []


def _pack_policy(
    context: Any, store: Path, pack: Pack, ix: Index
) -> list[dict[str, Any]]:
    target = f"pack:{pack.name}"
    path = f"packs/{pack.name}/pack.yaml"
    changed = query.pack_change_for(ix, target)
    if changed is not None:
        try:
            current = protected_snapshot(context, pack_name=pack.name)
        except ModelError:
            return []
        if changed["data"].get("candidate_snapshot") == current:
            return []
        return [
            _issue(
                "pack_policy_unapproved",
                path,
                "the protected pack policy changed after the pack was approved",
                fix=f"rail approve {target} --confirm \"APPROVE {target}\"",
            )
        ]
    local = store / "packs" / pack.name / "pack.yaml"
    if not local.is_file():
        return []
    try:
        shipped = load_pack(pack.name, store / _BUILTIN_PROBE)
    except ModelError:
        return []
    if pack_policy_digest(shipped) == pack_policy_digest(pack):
        return []
    return [
        _issue(
            "pack_policy_unapproved",
            path,
            "this pack's policy differs from the shipped policy and no "
            "pack change approved it",
            fix=f"rail approve {target} --confirm \"APPROVE {target}\"",
        )
    ]


def _drivers(store: Path) -> list[dict[str, Any]]:
    located = worktree_git_dir(store.absolute())
    if located is None:
        return []
    git_dir, root = located
    attributes = root / ".gitattributes"
    if not attributes.is_file():
        return []
    try:
        managed = "CONTEXT-RAIL-START" in attributes.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        managed = False
    if not managed:
        return []
    state = static_registration(git_dir, git_dir)
    found: list[dict[str, Any]] = []
    for key, value in sorted(state.items()):
        if value == "present":
            continue
        detail = "is not registered" if value == "missing" else "cannot be read statically"
        found.append(
            _issue(
                "missing_sync_driver",
                ".git/config",
                f"{key} {detail}",
                fix=_SYNC_INSTALL,
            )
        )
    return found


def _records(ix: Index) -> list[dict[str, Any]]:
    seen: dict[str, list[str]] = {}
    for path, value_json in ix.conn.execute(
        "SELECT f.path, f.value_json FROM fields f "
        "JOIN docs d ON d.path = f.path "
        "WHERE f.key = 'id' AND d.primitive = 'record' AND d.broken = 0 "
        "ORDER BY f.path"
    ):
        try:
            record_id = json.loads(value_json)
        except ValueError:
            continue
        if isinstance(record_id, str) and record_id:
            seen.setdefault(record_id, []).append(path)
    found: list[dict[str, Any]] = []
    for record_id, paths in sorted(seen.items()):
        if len(paths) <= 1:
            continue
        found.append(
            _issue(
                "duplicate_record_id",
                paths[0],
                f"record id {record_id!r} is claimed by {len(paths)} files: "
                + ", ".join(paths),
            )
        )
    return found


def _versions(store: Path) -> list[dict[str, Any]]:
    folder = store / "records"
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") not in _VERSIONED_KINDS:
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        if _version_matches(data, "before") and _version_matches(data, "after"):
            continue
        found.append(
            _issue(
                "version_history_mismatch",
                path.relative_to(store).as_posix(),
                "a versioned change carries text that does not hash to its digest",
            )
        )
    return found


def _version_matches(data: dict[str, Any], side: str) -> bool:
    text = data.get(f"{side}_text")
    digest = data.get(f"{side}_digest")
    if not isinstance(text, str) or not isinstance(digest, str):
        return False
    return hashlib.sha256(text.encode("utf-8")).hexdigest() == digest


def _sync_intent(store: Path) -> list[dict[str, Any]]:
    if not intent_path(store).is_file():
        return []
    return [
        _issue(
            "interrupted_sync",
            "sync/intent.json",
            "an interrupted sync is waiting to be resumed",
            fix="rail ops sync",
        )
    ]


def _issue(
    code: str, path: str, message: str, fix: str | None = None
) -> dict[str, Any]:
    return {
        "severity": SEVERITY[code],
        "code": code,
        "path": path,
        "message": message,
        "fix": fix,
    }
