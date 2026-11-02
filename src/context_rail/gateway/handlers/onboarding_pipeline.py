"""Read the project state an onboarding run reconciles against."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from context_rail.core.util import load_yaml
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, load_manifest, render_path
from context_rail.store.approval import APPROVAL_KIND
from context_rail.store.doc import parse_doc

__all__ = ["probe"]

_CHARTER = "charter"


def probe(project_root: Path, store: Path, pack_name: str = "basic") -> dict[str, Any]:
    """Describe the store, picture, approval, workspace, agents and git state."""
    project_root = Path(project_root)
    store = Path(store)
    store_exists = (store / "rail.yaml").is_file()
    manifest: Manifest | None = None
    pack: str | None = pack_name
    error: str | None = None
    if store_exists:
        try:
            manifest = load_manifest(store)
            pack = manifest.pack
        except (ModelError, OSError, ValueError, yaml.YAMLError) as exc:
            error = str(exc)
            pack = _raw_pack(store)
    picture = _picture(store, manifest)
    return {
        "project": str(project_root),
        "store": {
            "path": str(store),
            "exists": store_exists,
            "pack": pack,
            "error": error,
        },
        "picture": picture,
        "approval": {"state": _approval_state(store)},
        "workspace": {"exists": _workspace_exists(store)},
        "agents": {"exists": _agents_exist(project_root)},
        "git": {"exists": _git_checkout(project_root)},
    }


def _raw_pack(store: Path) -> str | None:
    try:
        data = load_yaml((store / "rail.yaml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    name = data.get("pack") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def _picture(store: Path, manifest: Manifest | None) -> dict[str, Any]:
    if manifest is None:
        return {"exists": False, "slugs": []}
    try:
        rel = render_path(
            manifest, primitive="policy", preset=_CHARTER, slug=_CHARTER
        )
    except ModelError:
        return {"exists": False, "slugs": []}
    path = store / rel
    if not path.is_file():
        return {"exists": False, "slugs": []}
    doc = parse_doc(rel, path.read_bytes())
    return {"exists": True, "slugs": _slugs(doc.fields.get("project"))}


def _slugs(project: Any) -> list[str]:
    if not isinstance(project, dict):
        return []
    found: list[str] = []
    for key in ("goal", "vision"):
        value = project.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    scope = project.get("scope")
    if isinstance(scope, list):
        found.extend(value for value in scope if isinstance(value, str) and value)
    wont = project.get("wont")
    if isinstance(wont, list):
        found.extend(value for value in wont if isinstance(value, str) and value)
    return list(dict.fromkeys(found))


def _approval_state(store: Path) -> str:
    records = store / "records"
    if not records.is_dir():
        return "missing"
    for path in sorted(records.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != APPROVAL_KIND:
            continue
        data = payload.get("data")
        target = data.get("target") if isinstance(data, dict) else None
        about = payload.get("about")
        if target == _CHARTER or (
            isinstance(about, list) and _CHARTER in about
        ):
            return "recorded"
    return "missing"


def _workspace_exists(store: Path) -> bool:
    workspace = store / "workspace"
    return workspace.is_dir() and any(path.is_file() for path in workspace.rglob("*"))


def _agents_exist(project_root: Path) -> bool:
    return (project_root / "AGENTS.md").is_file() or (
        project_root / ".agents" / "skills"
    ).is_dir()


def _git_checkout(project_root: Path) -> bool:
    for directory in (project_root, *project_root.parents):
        if (directory / ".git").exists():
            return True
    return False
