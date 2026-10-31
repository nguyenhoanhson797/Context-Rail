"""Pack tools: list, show, validate, create and the approval-bound switch."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from context_rail.gateway.confirmation import (
    confirmation_phrase,
    validate_confirmation,
)
from context_rail.kernel.packs import parse_map_entries, plan_status_map
from context_rail.model.errors import ModelError
from context_rail.model.pack import (
    check_pack_name,
    effective_measurement_min_runs,
    load_pack,
)
from context_rail.store import query
from context_rail.store.approval import (
    approval_state,
    protected_snapshot,
    snapshot_digest,
)
from context_rail.store.doc import etag
from context_rail.store.frontmatter_edit import join_frontmatter, split_frontmatter
from context_rail.store.location import local_dir
from context_rail.store.lock import StoreLockTimeout, file_lock
from context_rail.store.packs import (
    copy_plan,
    pack_bytes,
    pack_origins,
    pack_resources,
    pack_root,
)
from context_rail.store.write import (
    Op,
    WriteRefused,
    commit,
    replay,
    store_inventory,
)
from context_rail.runner.dispatch import (
    DISPATCH_DIR,
    LOCK_DIR,
    acquire_tick_lock,
)

if TYPE_CHECKING:  # imported lazily by the gateway only when a pack tool runs
    from context_rail.gateway.gateway import Context

LIST_CAP = 50
_TEMPLATES = ("item", "note", "record", "check")


def pack_list(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Every installed pack, bounded, with its origin and the active one."""
    origins = pack_origins(ctx.store)
    rows = [
        {"name": name, "origin": origins[name], "active": name == ctx.pack.name}
        for name in sorted(origins)
    ]
    return {"packs": rows[:LIST_CAP], "omitted_count": max(0, len(rows) - LIST_CAP)}


def pack_show(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """One pack's resources, origin, manifest etag and approval state."""
    name = _known(ctx.store, args["name"])
    data = pack_bytes(ctx.store, name)
    return {
        "pack": name,
        "resources": pack_resources(ctx.store, name),
        "origin": pack_origins(ctx.store)[name],
        "etag": etag(data),
        "approval": approval_state(ctx, f"pack:{name}"),
    }


def pack_validate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """A read report, never a refusal: the pack is valid or the problems are."""
    name = args["name"]
    problems: list[dict[str, str]] = []
    try:
        check_pack_name(name)
        if name not in pack_origins(ctx.store):
            problems.append({"path": "pack.yaml", "message": f"unknown pack {name!r}"})
            return {"valid": False, "problems": problems}
        load_pack(name, ctx.store)
        root = pack_root(ctx.store, name)
        if root is None:
            problems.append({"path": "pack.yaml", "message": f"unknown pack {name!r}"})
            return {"valid": False, "problems": problems}
        for template in _TEMPLATES:
            if not root.joinpath("templates", f"{template}.md").is_file():
                problems.append(
                    {
                        "path": f"templates/{template}.md",
                        "message": f"pack {name!r} ships no {template} template",
                    }
                )
    except ModelError as error:
        problems.append({"path": "pack.yaml", "message": error.message})
    return {"valid": not problems, "problems": problems}


def pack_create(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Copy a pack into an unselected draft, in one commit."""
    saved = replay(ctx.store, args.get("key"))
    if saved is not None:
        data = _record_payload(ctx.store, saved.get("record"))
        return {
            "name": data.get("name"),
            "paths": list(data.get("paths") or []),
            "record": saved.get("record"),
            "approval": "pending",
        }
    name = args["name"]
    source = args["from"]
    check_pack_name(name)
    destination = Path(ctx.store) / "packs" / name / "pack.yaml"
    if destination.exists():
        raise WriteRefused(
            "CONFLICT", f"pack {name!r} already exists", {"pack": name}
        )
    ops = copy_plan(ctx.store, name, source)
    record = {
        "kind": "pack.created",
        "preset": "event",
        "data": {
            "schema": 1,
            "name": name,
            "from": source,
            "paths": [op.path for op in ops],
        },
    }
    result = commit(ctx.store, ctx.manifest, ops, record, actor=actor, key=args.get("key"))
    return {
        "name": name,
        "paths": [op.path for op in ops],
        "record": result["record"],
        "approval": "pending",
    }


def pack_use(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Switch the selected pack in one commit under the approval guard."""
    store = ctx.store
    saved = replay(store, args.get("key"))
    if saved is not None:
        return _replay_value(ctx, saved)

    name = args["name"]
    dry_run = args["dry_run"] is True
    if not dry_run:
        reason = validate_confirmation("pack_use", name, args.get("confirm"))
        if reason is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"target": name, "confirm": confirmation_phrase("pack_use", name)},
            )
    manifest_path = store / "rail.yaml"
    current_manifest = manifest_path.read_bytes()
    if args["expect"] != etag(current_manifest):
        raise WriteRefused(
            "CONFLICT",
            "rail.yaml changed since it was read",
            {"path": "rail.yaml", "expect": args["expect"]},
        )
    _refuse_pending_intents(store)
    target = _load_target(store, name)

    with _quiet(store):
        items = query.items_for_migration(ctx.index)
        current = ctx.pack
        explicit, duplicates = parse_map_entries(args["map"])
        if duplicates:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "duplicate or malformed status map entries",
                {"problems": list(duplicates)},
            )
        mapping = plan_status_map(
            current, target, frozenset(row["status"] for row in items), explicit
        )
        if mapping.problems:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the store's statuses cannot be mapped into the target pack",
                {"problems": [list(problem) for problem in mapping.problems]},
            )
        unsupported = _unsupported_fields(items, current, target)
        if unsupported:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the store uses presets or fields the target pack does not declare",
                {"unsupported": unsupported},
            )

        ops = []
        changes: list[dict[str, str]] = []
        for row in items:
            current_data = (store / row["path"]).read_bytes()
            current_etag = etag(current_data)
            new_status = mapping.mapping.get(row["status"], row["status"])
            data = current_data
            if new_status != row["status"]:
                data = _rewrite_status(current_data, new_status)
                changes.append(
                    {"slug": row["slug"], "from": row["status"], "to": new_status}
                )
            ops.append(Op(row["path"], data, current_etag))
        changes.sort(key=lambda row: row["slug"])
        manifest_bytes = _rewrite_manifest(current_manifest, target.name)
        ops.append(Op("rail.yaml", manifest_bytes, etag(current_manifest)))
        candidate = protected_snapshot(ctx, pack_name=target.name)
        value = {
            "from": current.name,
            "to": target.name,
            "status_map": dict(mapping.mapping),
            "changes": changes,
            "warnings": [],
            "manifest_etag": etag(manifest_bytes),
            "record": None,
        }
        if dry_run:
            return value

        record = {
            "kind": "pack.changed",
            "preset": "event",
            "about": [row["slug"] for row in changes],
            "data": {
                "schema": 1,
                "from": current.name,
                "to": target.name,
                "status_map": dict(mapping.mapping),
                "changed_slugs": [row["slug"] for row in changes],
                "changed_count": len(changes),
                "approval_target": f"pack:{target.name}",
                "candidate_snapshot": candidate,
                "candidate_digest": snapshot_digest(candidate),
            },
        }
        inventory = _after_planning(store_inventory(store))
        result = commit(
            store,
            ctx.manifest,
            ops,
            record,
            actor=actor,
            key=args.get("key"),
            inventory=inventory,
        )
        value["record"] = result["record"]
        ctx.close()
        return value


def _after_planning(
    inventory: tuple[tuple[str, str], ...] | None,
) -> tuple[tuple[str, str], ...] | None:
    """The inventory the locked publication must still match."""
    return inventory


def _quiet(store: Path) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def guard() -> Iterator[None]:
        with acquire_tick_lock(store):
            folder = local_dir(store) / DISPATCH_DIR / LOCK_DIR
            if folder.is_dir():
                for path in sorted(folder.glob("*.lock")):
                    try:
                        with file_lock(path, timeout=0):
                            pass
                    except StoreLockTimeout:
                        raise WriteRefused(
                            "CONFLICT",
                            "a dispatch is still running",
                            {"lock": str(path)},
                        ) from None
            yield

    return guard()


def _refuse_pending_intents(store: Path) -> None:
    folder = local_dir(store) / "intents"
    pending = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if pending:
        raise WriteRefused(
            "INVALID_STATE",
            "an interrupted write is waiting for repair",
            {"intents": [path.name for path in pending]},
        )


def _load_target(store: Path, name: str) -> Any:
    if name not in pack_origins(store):
        raise WriteRefused("NOT_FOUND", f"unknown pack {name!r}", {"pack": name})
    return load_pack(name, store)


def _known(store: Path, name: str) -> str:
    if name not in pack_origins(store):
        raise WriteRefused("NOT_FOUND", f"unknown pack {name!r}", {"pack": name})
    return name


def _unsupported_fields(items: list[dict[str, Any]], source: Any, target: Any) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for row in items:
        source_preset = source.presets.get(row["preset"])
        target_preset = target.presets.get(row["preset"])
        if target_preset is None:
            problems.append(
                {"path": row["path"], "field": "preset", "value": row["preset"]}
            )
            continue
        declared = set(source_preset.fields) if source_preset is not None else set()
        for key in sorted(set(row["fields"]) & declared):
            if key not in target_preset.fields:
                problems.append(
                    {"path": row["path"], "field": key, "value": row["fields"][key]}
                )
    return problems


def _rewrite_status(data: bytes, status: str) -> bytes:
    text = data.decode("utf-8")
    split = split_frontmatter(text)
    if split is None:
        raise WriteRefused(
            "VALIDATION_FAILED", "an Item has no frontmatter to rewrite"
        )
    block, rest = split
    lines = block.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("status:"):
            lines[index] = f"status: {status}"
            break
    else:
        lines.append(f"status: {status}")
    return join_frontmatter("\n".join(lines), rest).encode("utf-8")


def _rewrite_manifest(data: bytes, pack_name: str) -> bytes:
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("pack:"):
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = f"pack: {pack_name}{ending}"
            return "".join(lines).encode("utf-8")
    raise WriteRefused("VALIDATION_FAILED", "rail.yaml has no pack line")


def _replay_value(ctx: Context, saved: dict[str, Any]) -> dict[str, Any]:
    record_id = saved.get("record")
    data = _pack_change_data(ctx.store, record_id)
    mapping = data.get("status_map") or {}
    reverse: dict[str, str] = {}
    for old, new in mapping.items():
        reverse.setdefault(new, old)
    statuses = {
        row["slug"]: row["status"] for row in query.items_for_migration(ctx.index)
    }
    changes = []
    for slug in data.get("changed_slugs") or []:
        current = statuses.get(slug)
        changes.append(
            {"slug": slug, "from": reverse.get(current, current), "to": current}
        )
    return {
        "from": data.get("from"),
        "to": data.get("to"),
        "status_map": dict(mapping),
        "changes": changes,
        "warnings": [],
        "manifest_etag": etag((ctx.store / "rail.yaml").read_bytes()),
        "record": record_id,
    }


def _pack_change_data(store: Path, record_id: Any) -> dict[str, Any]:
    return _record_payload(store, record_id)


def _record_payload(store: Path, record_id: Any) -> dict[str, Any]:
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
