"""Bootstrap a store: the pinned layout, written through ``commit``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.defaults import DEFAULT_PACK
from context_rail.core.util import load_yaml
from context_rail.model.manifest import Manifest, initial_manifest
from context_rail.model.pack import canonical_pack_name, load_pack
from context_rail.store.location import confine
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:  # the gateway imports this module only when init is invoked
    from context_rail.gateway.gateway import Context

BASE_FOLDERS = ("packs", "templates", "workspace/skills")
GITIGNORE = b".rail-local/\n"


def pinned_folders(manifest: Manifest) -> tuple[str, ...]:
    """The static primitive-placement folders a fresh store should create empty."""
    found = set(BASE_FOLDERS)
    for template in manifest.placement.values():
        prefix = _static_prefix(template)
        if prefix:
            found.add(prefix)
    return tuple(sorted(found))


def _static_prefix(template: str) -> str:
    parts = template.split("/")
    literal: list[str] = []
    for part in parts:
        if "{" in part:
            break
        literal.append(part)
    else:
        literal = literal[:-1]
    return "/".join(literal)


def scaffold(_ctx: Context | None, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create ``<dir>/<root>`` with the pack's manifest and pinned folders."""
    result = skeleton(_ctx, args, actor, allow_existing=False)
    return {"store": result["store"], "pack": result["pack"]}


def skeleton(
    _ctx: Context | None,
    args: dict[str, Any],
    actor: str,
    *,
    allow_existing: bool,
) -> dict[str, Any]:
    """Write the absent skeleton parts, optionally resuming an existing store."""
    directory = Path(args["dir"] or Path.cwd())
    store = confine(directory, args["root"])
    requested_raw = args.get("pack")
    requested = (
        requested_raw.strip()
        if isinstance(requested_raw, str) and requested_raw.strip()
        else None
    )
    stored = _stored_pack(store) if (store / "rail.yaml").exists() else None
    pack_name = requested or stored or DEFAULT_PACK
    pack = load_pack(pack_name, store)
    manifest, data = initial_manifest(pack.name, store)
    saved = replay(store, args.get("key"))
    if saved is not None:
        return {
            "store": str(store),
            "pack": _stored_pack(store) or pack.name,
            "state": "unchanged",
            "written": [],
        }
    if (store / "rail.yaml").exists():
        if not allow_existing:
            raise WriteRefused(
                "CONFLICT",
                "a store already exists here",
                {"path": str(store / "rail.yaml")},
            )
        if stored is None:
            raise WriteRefused(
                "CONFLICT",
                "the existing rail.yaml cannot be read",
                {"path": str(store / "rail.yaml")},
            )
        if requested is not None and canonical_pack_name(requested) != canonical_pack_name(stored):
            raise WriteRefused(
                "CONFLICT",
                f"the store uses pack {stored!r}, not {pack.name!r}",
                {"stored": stored, "requested": pack.name},
            )
        written: list[str] = []
        if not (store / ".gitignore").exists():
            result = commit(
                store,
                manifest,
                [Op(".gitignore", GITIGNORE, ABSENT)],
                None,
                actor=actor,
                key=args.get("key"),
            )
            written = result["written"]
        _make_pinned_folders(store, manifest)
        return {
            "store": str(store),
            "pack": stored,
            "state": "written" if written else "unchanged",
            "written": written,
        }
    ops = [
        Op("rail.yaml", yaml.safe_dump(data, sort_keys=False).encode("utf-8"), ABSENT),
        Op(".gitignore", GITIGNORE, ABSENT),
    ]
    result = commit(store, manifest, ops, None, actor=actor, key=args.get("key"))
    _make_pinned_folders(store, manifest)
    return {
        "store": str(store),
        "pack": pack.name,
        "state": "written",
        "written": result["written"],
    }


def _make_pinned_folders(store: Path, manifest: Manifest) -> None:
    # commit writes files, and a fresh store's pinned folders hold none yet, so
    # they are made here instead of as placeholders the index would count.
    for folder in pinned_folders(manifest):
        (store / folder).mkdir(parents=True, exist_ok=True)


def _stored_pack(store: Path) -> str | None:
    """The pack a replayed init already recorded, when rail.yaml is readable."""
    try:
        data = load_yaml((store / "rail.yaml").read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError):
        return None
    name = data.get("pack") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None
