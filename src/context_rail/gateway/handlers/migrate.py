"""Import a v1/v2 legacy store into a fresh v3 store in one commit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.util import now_iso
from context_rail.gateway.handlers.init import GITIGNORE, PINNED_FOLDERS
from context_rail.model.manifest import DEFAULT_MANIFEST, manifest_from_mapping
from context_rail.model.pack import load_pack
from context_rail.store.legacy import read_legacy
from context_rail.store.location import confine
from context_rail.store.migration import plan_migration
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:  # the gateway imports this module only when migrate is invoked
    from context_rail.gateway.gateway import Context

__all__ = ["migrate"]


def migrate(ctx: Context | None, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Read a legacy tree, then write a v3 store through one commit."""
    if actor != "human":
        raise WriteRefused(
            "PERMISSION_DENIED",
            "migrating a legacy store is a human-only action",
            {"tool": "migrate", "actor": actor},
        )
    source = Path(args["source"])
    directory = Path(args["dir"] or Path.cwd())
    target = confine(directory, args["root"])
    key = args.get("key")
    saved = replay(target, key)
    if saved is not None:
        return _replay_summary(target, source, saved)
    if (target / "rail.yaml").exists():
        raise WriteRefused(
            "CONFLICT",
            "a store already exists at the migration target",
            {"path": str(target / "rail.yaml")},
        )
    pack = load_pack(args["pack"], target)
    legacy = read_legacy(source)
    manifest = manifest_from_mapping({**DEFAULT_MANIFEST, "pack": pack.name}, target)
    plan = plan_migration(
        legacy, pack=pack, manifest=manifest, at=now_iso()
    )
    summary = {
        "source": str(source.resolve()),
        "target": str(target),
        "pack": pack.name,
        "schema": legacy.schema,
        "documents": plan.counts["documents"],
        "records": plan.counts["records"],
        "warnings": list(plan.warnings),
        "written": [],
        "record": None,
    }
    if args["dry_run"]:
        return summary

    ops = [
        Op("rail.yaml", _manifest_bytes(pack.name), ABSENT),
        Op(".gitignore", GITIGNORE, ABSENT),
        *(Op(document.rel, document.data, ABSENT) for document in plan.documents),
        *(Op(record.rel, record.data, ABSENT) for record in plan.records),
    ]
    record = {
        "kind": "migration.completed",
        "about": [],
        "data": {
            "schema": 1,
            "source": str(source.resolve()),
            "source_schema": legacy.schema,
            "pack": pack.name,
            "documents": plan.counts["documents"],
            "records": plan.counts["records"],
            "warnings": list(plan.warnings),
        },
    }
    result = commit(target, manifest, ops, record, actor=actor, key=key)
    for folder in PINNED_FOLDERS:
        (target / folder).mkdir(parents=True, exist_ok=True)
    summary["written"] = list(result["written"])
    summary["record"] = result["record"]
    return summary


def _manifest_bytes(pack_name: str) -> bytes:
    data = {**DEFAULT_MANIFEST, "pack": pack_name}
    text = yaml.safe_dump(data, sort_keys=False)
    return text.encode("utf-8")


def _replay_summary(target: Path, source: Path, saved: dict[str, Any]) -> dict[str, Any]:
    record_id = saved.get("record")
    data = _record_data(target, record_id)
    return {
        "source": data.get("source") or str(source.resolve()),
        "target": str(target),
        "pack": data.get("pack"),
        "schema": data.get("source_schema"),
        "documents": data.get("documents", 0),
        "records": data.get("records", 0),
        "warnings": list(data.get("warnings") or []),
        "written": list(saved.get("written") or []),
        "record": record_id,
    }


def _record_data(store: Path, record_id: Any) -> dict[str, Any]:
    if not isinstance(record_id, str) or not record_id:
        return {}
    for path in sorted((store / "records").rglob(f"{record_id}.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            return data
    return {}
