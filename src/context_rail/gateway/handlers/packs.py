"""Pack tools: list, show, validate, create and the approval-bound switch."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterator

import yaml

from context_rail.core.util import load_yaml
from context_rail.gateway.confirmation import (
    confirmation_phrase,
    validate_confirmation,
)
from context_rail.kernel.packs import parse_map_entries, plan_status_map
from context_rail.model.errors import ModelError
from context_rail.model.manifest import initial_manifest, manifest_from_mapping
from context_rail.model.metadata import ENGINE_PRESETS
from context_rail.model.pack import (
    REQUIRED_PRESETS,
    check_pack_name,
    effective_measurement_min_runs,
    load_pack,
    pack_layout,
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
    installed_pack_name,
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
    active = installed_pack_name(ctx.store, ctx.pack.name)
    rows = [
        {"name": name, "origin": origins[name], "active": name == active}
        for name in sorted(origins)
    ]
    return {"packs": rows[:LIST_CAP], "omitted_count": max(0, len(rows) - LIST_CAP)}


def pack_show(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """One pack's resources, origin, manifest etag and approval state."""
    requested = args["name"]
    name = _known(ctx.store, requested)
    data = pack_bytes(ctx.store, name)
    # A store keeps the pack name its approval was recorded for, so an alias it
    # is still running must report the approval of that same target.
    target = requested if requested == ctx.manifest.pack else name
    return {
        "pack": name,
        "resources": pack_resources(ctx.store, name),
        "origin": pack_origins(ctx.store)[name],
        "etag": etag(data),
        "approval": approval_state(ctx, f"pack:{target}"),
    }


def pack_validate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """A read report, never a refusal: the pack is valid or the problems are."""
    requested = args["name"]
    problems: list[dict[str, str]] = []
    try:
        check_pack_name(requested)
        name = installed_pack_name(ctx.store, requested)
        if name is None:
            problems.append(
                {"path": "pack.yaml", "message": f"unknown pack {requested!r}"}
            )
            return {"valid": False, "problems": problems}
        pack = load_pack(name, ctx.store)
        root = pack_root(ctx.store, name)
        if root is None:
            problems.append(
                {"path": "pack.yaml", "message": f"unknown pack {requested!r}"}
            )
            return {"valid": False, "problems": problems}
        for preset in REQUIRED_PRESETS:
            if preset not in pack.presets:
                problems.append(
                    {
                        "path": "pack.yaml",
                        "message": f"pack {name!r} declares no required preset {preset!r}",
                    }
                )
        problems.extend(_engine_metadata_problems(pack, name))
        templates = {f"templates/{template}.md" for template in _TEMPLATES}
        templates.update(
            preset.template.replace("{primitive}", preset.primitive)
            for preset in pack.presets.values()
        )
        for template in sorted(templates):
            if not root.joinpath(*PurePosixPath(template).parts).is_file():
                problems.append(
                    {
                        "path": template,
                        "message": f"pack {name!r} ships no {template}",
                    }
                )
        try:
            has_layout = pack_layout(name, ctx.store) is not None
            if has_layout:
                initial_manifest(name, ctx.store)
        except ModelError as error:
            problems.append({"path": "layout.yaml", "message": error.message})
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
    check_pack_name(source)
    resolved = installed_pack_name(ctx.store, source)
    if resolved is None:
        raise WriteRefused("NOT_FOUND", f"unknown pack {source!r}", {"pack": source})
    destination = Path(ctx.store) / "packs" / name / "pack.yaml"
    if destination.exists():
        raise WriteRefused(
            "CONFLICT", f"pack {name!r} already exists", {"pack": name}
        )
    ops = copy_plan(ctx.store, name, resolved)
    record = {
        "kind": "pack.created",
        "preset": "event",
        "data": {
            "schema": 1,
            "name": name,
            "from": resolved,
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
    manifest_path = store / "rail.yaml"
    current_manifest = manifest_path.read_bytes()
    if args["expect"] != etag(current_manifest):
        raise WriteRefused(
            "CONFLICT",
            "rail.yaml changed since it was read",
            {"path": "rail.yaml", "expect": args["expect"]},
        )
    target = _load_target(store, name)
    _refuse_pending_intents(store)

    with _quiet(store):
        if not dry_run:
            reason = validate_confirmation("pack_use", name, args.get("confirm"))
            if reason is not None:
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    reason,
                    {"target": name, "confirm": confirmation_phrase("pack_use", name)},
                )
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
        engine_metadata = _engine_metadata_problems(target, target.name)
        if engine_metadata:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the target pack needs defaults on engine-created presets",
                {"unsupported": engine_metadata},
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
        _require_manifest_served(store, manifest_bytes, target.name)
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
    if installed_pack_name(store, name) is None:
        raise WriteRefused("NOT_FOUND", f"unknown pack {name!r}", {"pack": name})
    return load_pack(name, store)


def _known(store: Path, name: str) -> str:
    resolved = installed_pack_name(store, name)
    if resolved is None:
        raise WriteRefused("NOT_FOUND", f"unknown pack {name!r}", {"pack": name})
    return resolved


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
        for name in target_preset.required:
            if name in target_preset.defaults:
                continue
            if name not in row["fields"] or row["fields"][name] in ("", [], {}, None):
                problems.append(
                    {"path": row["path"], "field": name, "value": None}
                )
    return problems


def _engine_metadata_problems(pack: Any, name: str) -> list[dict[str, str]]:
    """Engine-written presets need a default for every required field."""
    problems: list[dict[str, str]] = []
    for preset_name in sorted(ENGINE_PRESETS):
        preset = pack.presets.get(preset_name)
        if preset is None:
            continue
        missing = [field for field in preset.required if field not in preset.defaults]
        if missing:
            problems.append(
                {
                    "path": f"presets.{preset_name}",
                    "message": (
                        f"pack {name!r} requires "
                        + ", ".join(missing)
                        + " on an engine-created preset; give it a default"
                    ),
                }
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
    return join_frontmatter(_set_scalar(block, "status", status), rest).encode("utf-8")


def _rewrite_manifest(data: bytes, pack_name: str) -> bytes:
    text = data.decode("utf-8")
    return _set_scalar(text, "pack", pack_name).encode("utf-8")


def _require_manifest_served(store: Path, data: bytes, pack_name: str) -> None:
    """Refuse a switch whose manifest the target pack could never serve."""
    try:
        mapping = load_yaml(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read the manifest for pack {pack_name!r}: {error}",
            {"pack": pack_name, "path": "rail.yaml"},
        ) from None
    try:
        manifest_from_mapping(mapping, store)
    except ModelError as error:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"pack {pack_name!r} cannot serve this store's manifest: {error.message}",
            {"pack": pack_name, "path": "rail.yaml"},
        ) from None


_KEY_LINE = re.compile(r"^([ \t]*)([A-Za-z0-9_.-]+):(.*)$")
_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _set_scalar(text: str, key: str, value: str) -> str:
    """Replace one mapping key's scalar, preserving indentation, quote and comment."""
    if not _SAFE_SCALAR.match(value):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{value!r} is not a safe {key} value",
            {"key": key},
        )
    lines = text.split("\n")
    matches = [index for index, line in enumerate(lines) if _key_match(line, key)]
    if len(matches) != 1:
        reason = "duplicate" if matches else "missing"
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the {key} line is {reason}",
            {"key": key},
        )
    index = matches[0]
    line = lines[index]
    ending = "\r" if line.endswith("\r") else ""
    if ending:
        line = line[:-1]
    match = _KEY_LINE.match(line)
    assert match is not None
    lines[index] = f"{match.group(1)}{key}:{_replace_value(match.group(3), value)}{ending}"
    return "\n".join(lines)


def _key_match(line: str, key: str) -> bool:
    match = _KEY_LINE.match(line[:-1] if line.endswith("\r") else line)
    return match is not None and match.group(2) == key


def _replace_value(rest: str, value: str) -> str:
    lead = re.match(r"^[ \t]*", rest)
    lead_text = lead.group(0) if lead is not None else ""
    body = rest[len(lead_text) :]
    if body[:1] in ("'", '"'):
        quote = body[0]
        close = body.find(quote, 1)
        if close < 0:
            raise WriteRefused("VALIDATION_FAILED", "a quoted value is not closed")
        tail = body[close + 1 :]
        if tail.strip() and not tail.lstrip().startswith("#"):
            raise WriteRefused("VALIDATION_FAILED", "a quoted value has trailing text")
        return f"{lead_text}{quote}{value}{quote}{tail}"
    comment = re.search(r"[ \t]+#", body)
    tail = body[comment.start() :] if comment is not None else ""
    return f"{lead_text}{value}{tail}"


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
