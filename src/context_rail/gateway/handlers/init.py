"""Bootstrap a store: the pinned layout, written through ``commit``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.util import load_yaml
from context_rail.model.manifest import DEFAULT_MANIFEST, manifest_from_mapping
from context_rail.model.pack import load_pack
from context_rail.store.location import confine
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:  # the gateway imports this module only when init is invoked
    from context_rail.gateway.gateway import Context

PINNED_FOLDERS = (
    "items",
    "notes",
    "checks",
    "records",
    "policies",
    "workspace/profiles",
    "workspace/skills",
    "packs",
    "templates",
)
GITIGNORE = b".rail-local/\n"


def init(_ctx: Context | None, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create ``<dir>/<root>`` with the default manifest and pinned folders."""
    directory = Path(args["dir"] or Path.cwd())
    store = confine(directory, args["root"])
    pack = load_pack(args["pack"], store)
    data = {**DEFAULT_MANIFEST, "pack": pack.name}
    manifest = manifest_from_mapping(data, store)
    if replay(store, args["key"]) is not None:
        return {"store": str(store), "pack": _stored_pack(store) or pack.name}
    if (store / "rail.yaml").exists():
        raise WriteRefused(
            "CONFLICT",
            "a store already exists here",
            {"path": str(store / "rail.yaml")},
        )
    ops = [
        Op("rail.yaml", yaml.safe_dump(data, sort_keys=False).encode("utf-8"), ABSENT),
        Op(".gitignore", GITIGNORE, ABSENT),
    ]
    commit(store, manifest, ops, None, actor=actor, key=args["key"])
    # commit writes files, and a fresh store's pinned folders hold none yet, so
    # they are made here instead of as placeholders the index would count.
    for folder in PINNED_FOLDERS:
        (store / folder).mkdir(parents=True, exist_ok=True)
    return {"store": str(store), "pack": pack.name}


def _stored_pack(store: Path) -> str | None:
    """The pack a replayed init already recorded, when rail.yaml is readable."""
    try:
        data = load_yaml((store / "rail.yaml").read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError):
        return None
    name = data.get("pack") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None
