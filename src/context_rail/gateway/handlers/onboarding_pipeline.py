"""Read the project state an onboarding run reconciles against."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.util import load_yaml
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, load_manifest, render_path
from context_rail.store.approval import APPROVAL_KIND
from context_rail.store.doc import parse_doc
from context_rail.store.write import commit

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["approval_stage", "picture_stage", "probe", "workspace_seed"]

_PICTURE_YES = ("y", "yes", "true")
_INTERVIEW_COMMAND = "rail init --interview"
_APPROVE_COMMAND = (
    'rail --actor human approve charter --confirm "APPROVE charter" '
    "--confirmer NAME"
)

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


def picture_stage(
    context: Context,
    project_root: Path,
    *,
    actor: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Write the selected picture source, or skip when one already exists."""
    from context_rail.gateway.confirmation import validate_confirmation
    from context_rail.gateway.handlers.onboarding import (
        _interview,
        _is_tty,
        _materialise,
        _mode,
        read_answers,
    )

    found = probe(project_root, context.store, context.pack.name)
    if found["picture"]["exists"]:
        return {
            "state": "skipped",
            "reason": "picture_present",
            "slugs": found["picture"]["slugs"],
        }
    mode = _mode(args)
    if mode == "scaffold":
        if not _is_tty() or actor != "human":
            return {
                "state": "skipped",
                "reason": "no_picture_source",
                "command": _INTERVIEW_COMMAND,
            }
        answer = builtins.input("No picture yet - enter it now? [Y/n]: ").strip().lower()
        if answer not in _PICTURE_YES:
            return {
                "state": "skipped",
                "reason": "deferred",
                "command": _INTERVIEW_COMMAND,
            }
        mode = "interview"
    if mode == "draft":
        from context_rail.store.write import WriteRefused

        if actor != "cto":
            raise WriteRefused(
                "PERMISSION_DENIED", "only the CTO actor may propose a draft picture"
            )
        answers = read_answers(Path(args["draft_file"]))
        draft = True
    else:
        from context_rail.store.write import WriteRefused

        if actor != "human":
            raise WriteRefused(
                "PERMISSION_DENIED", f"{mode} onboarding needs the human actor"
            )
        if mode == "answers":
            reason = validate_confirmation("approve", "charter", args.get("confirm"))
            if reason is not None:
                raise WriteRefused(
                    "VALIDATION_FAILED", reason, {"confirm": "APPROVE charter"}
                )
            answers = read_answers(Path(args["answers_file"]))
        else:
            answers = _interview(context.pack.name, project_root)
        draft = False
    result = _materialise(
        context.store,
        project_root,
        answers,
        pack_name=context.pack.name,
        key=None,
        actor=actor,
        draft=draft,
        include_workspace=False,
    )
    return {
        "state": "written",
        "picture": result["picture"],
        "written": result["written"],
        "charter": result["charter"],
    }


def approval_stage(
    context: Context,
    *,
    actor: str,
    actor_named: bool,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Offer the fresh approval phrase when the picture is waiting for one."""
    from context_rail.gateway.handlers.onboarding import _is_tty, approve
    from context_rail.store.write import WriteRefused

    try:
        state = context.approval["state"]
    except (ModelError, WriteRefused) as exc:
        return {
            "state": "refused",
            "reason": getattr(exc, "code", "VALIDATION_FAILED"),
            "message": str(exc),
            "command": _APPROVE_COMMAND,
        }
    if state == "approved":
        return {"state": "unchanged", "approval": "approved"}
    if state != "missing":
        return {
            "state": "skipped",
            "reason": f"approval_{state}",
            "command": _APPROVE_COMMAND,
        }
    if args.get("no_approve"):
        return {
            "state": "skipped",
            "reason": "no_approve",
            "command": _APPROVE_COMMAND,
        }
    if not actor_named or actor != "human":
        return {
            "state": "skipped",
            "reason": "actor_not_named",
            "command": _APPROVE_COMMAND,
        }
    if not _is_tty():
        return {
            "state": "skipped",
            "reason": "no_terminal",
            "command": _APPROVE_COMMAND,
        }
    confirmer = builtins.input("Confirmer: ").strip()
    if not confirmer:
        return {
            "state": "skipped",
            "reason": "no_confirmer",
            "command": _APPROVE_COMMAND,
        }
    phrase = builtins.input(
        "Type APPROVE charter to approve now (blank to skip): "
    ).strip()
    if not phrase:
        return {
            "state": "skipped",
            "reason": "deferred",
            "command": _APPROVE_COMMAND,
        }
    try:
        result = approve(
            context,
            {
                "target": "charter",
                "confirm": phrase,
                "confirmer": confirmer,
                "expect": None,
                "key": None,
            },
            actor,
        )
    except (ModelError, WriteRefused) as exc:
        return {
            "state": "refused",
            "reason": getattr(exc, "code", "VALIDATION_FAILED"),
            "message": str(exc),
            "command": _APPROVE_COMMAND,
        }
    return {
        "state": "approved",
        "approval": result.get("approval"),
        "target": result.get("target"),
    }


def workspace_seed(context: Context) -> dict[str, Any]:
    """Write the active pack's absent profiles and skill notes."""
    from context_rail.gateway.handlers.onboarding import agent_ops

    ops = [
        op
        for op in agent_ops(context.store, context.manifest, context.pack.name)
        if not (context.store / op.path).is_file()
    ]
    if not ops:
        return {"state": "unchanged", "written": []}
    result = commit(
        context.store,
        context.manifest,
        ops,
        None,
        actor=context.actor,
        index=context.index,
    )
    return {"state": "written", "written": result["written"]}


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
