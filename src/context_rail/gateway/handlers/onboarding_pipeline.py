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
from context_rail.model.pack import load_pack
from context_rail.store.approval import APPROVAL_KIND
from context_rail.store.doc import parse_doc
from context_rail.store.write import WriteRefused, commit

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = [
    "agents_stage",
    "approval_stage",
    "git_drivers_stage",
    "picture_stage",
    "probe",
    "render_card",
    "run",
    "workspace_seed",
]

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


def agents_stage(context: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Render the managed agent files, passing the adapter refusal through."""
    from context_rail.gateway.handlers.adapters import sync as adapters_sync

    try:
        result = adapters_sync(
            context,
            {
                "dry_run": False,
                "force": False,
                "confirm": None,
                "harness": args.get("harness") or [],
                "key": None,
            },
            context.actor,
        )
    except WriteRefused as exc:
        return {
            "state": "refused",
            "reason": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    if result.get("written"):
        return {"state": "written", "written": result["written"]}
    return {"state": "unchanged", "written": []}


def git_drivers_stage(
    context: Context, project_root: Path, args: dict[str, Any]
) -> dict[str, Any]:
    """Install the clone-local git drivers when the checkout asks for them."""
    from context_rail.gateway.handlers.onboarding import _is_tty
    from context_rail.gateway.handlers.sync import sync as sync_handler

    if not _git_checkout(project_root):
        return {"state": "skipped", "reason": "not_git"}
    if args.get("no_git_drivers"):
        return {"state": "skipped", "reason": "disabled"}
    if not _is_tty():
        return {
            "state": "skipped",
            "reason": "no_terminal",
            "command": "rail sync --install",
        }
    answer = builtins.input("Install git merge drivers? [y/N]: ").strip().lower()
    if answer not in _PICTURE_YES:
        return {
            "state": "skipped",
            "reason": "deferred",
            "command": "rail sync --install",
        }
    try:
        result = sync_handler(
            context,
            {"install": True, "remote": None, "branch": None, "key": None},
            context.actor,
        )
    except WriteRefused as exc:
        return {
            "state": "refused",
            "reason": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    return {"state": "installed", "paths": result.get("paths", [])}


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


def run(
    project_root: Path,
    store: Path,
    *,
    pack_name: str,
    actor: str,
    actor_named: bool,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile the project to the furthest onboarding state it can reach."""
    project_root = Path(project_root)
    store = Path(store)
    plan = probe(project_root, store, pack_name)
    if args.get("dry_run"):
        return _dry_receipt(project_root, store, plan)
    skeleton_args = {
        "dir": str(project_root),
        "root": args.get("root") or store.name,
        "pack": pack_name,
        "key": args.get("key"),
    }
    from context_rail.gateway.handlers.init import skeleton

    stages: dict[str, Any] = {
        "skeleton": skeleton(None, skeleton_args, actor, allow_existing=True)
    }
    manifest = load_manifest(store)
    from context_rail.gateway.gateway import Context

    context = Context(store=store, manifest=manifest, pack=load_pack(manifest.pack, store), actor=actor)
    try:
        try:
            stages["workspace"] = workspace_seed(context)
        except WriteRefused as exc:
            stages["workspace"] = _refused(exc)
        try:
            picture = picture_stage(context, project_root, actor=actor, args=args)
        except WriteRefused as exc:
            stages["picture"] = _refused(exc)
            stages["approval"] = {"state": "skipped", "reason": "picture_refused"}
            stages["agents"] = {"state": "skipped", "reason": "picture_refused"}
            stages["git_drivers"] = {"state": "skipped", "reason": "picture_refused"}
            return _receipt(project_root, store, context, stages, None)
        stages["picture"] = picture
        if picture.get("state") == "skipped" and picture.get("reason") in {
            "deferred",
            "no_picture_source",
        }:
            stages["approval"] = {"state": "skipped", "reason": "picture_deferred"}
            stages["agents"] = {"state": "skipped", "reason": "picture_deferred"}
            stages["git_drivers"] = {"state": "skipped", "reason": "picture_deferred"}
            return _receipt(project_root, store, context, stages, None)
        stages["approval"] = approval_stage(
            context, actor=actor, actor_named=actor_named, args=args
        )
        stages["agents"] = agents_stage(context, args)
        stages["git_drivers"] = git_drivers_stage(context, project_root, args)
        return _receipt(project_root, store, context, stages, _status(context, actor))
    finally:
        context.close()


def _status(context: Context, actor: str) -> dict[str, Any] | None:
    from context_rail.gateway.handlers.views import status

    try:
        return status(context, {}, actor)
    except (ModelError, OSError, ValueError, WriteRefused):
        return None


def _refused(exc: WriteRefused) -> dict[str, Any]:
    return {
        "state": "refused",
        "reason": exc.code,
        "message": exc.message,
        "details": exc.details,
    }


def _receipt(
    project_root: Path,
    store: Path,
    context: Context,
    stages: dict[str, Any],
    status: dict[str, Any] | None,
) -> dict[str, Any]:
    approved = (
        stages.get("approval", {}).get("state") == "approved"
        or context.approval["state"] == "approved"
    )
    return {
        "store": str(store),
        "pack": context.pack.name,
        "stages": stages,
        "status": status,
        "approval": "approved" if approved else "missing",
        "next": _next_steps(approved),
        "dry_run": False,
    }


def _next_steps(approved: bool) -> list[str]:
    steps = []
    if not approved:
        steps.append(_APPROVE_COMMAND)
    steps.extend(
        [
            'rail add task "..."',
            "rail cto brief",
            "rail ui serve",
        ]
    )
    return steps


def _dry_receipt(
    project_root: Path, store: Path, plan: dict[str, Any]
) -> dict[str, Any]:
    picture = plan["picture"]
    stages = {
        "skeleton": {
            "state": "unchanged" if plan["store"]["exists"] else "planned"
        },
        "workspace": {
            "state": "unchanged" if plan["workspace"]["exists"] else "planned"
        },
        "picture": (
            {
                "state": "skipped",
                "reason": "picture_present",
                "slugs": picture["slugs"],
            }
            if picture["exists"]
            else {"state": "planned"}
        ),
        "approval": (
            {"state": "unchanged", "approval": "approved"}
            if plan["approval"]["state"] == "recorded"
            else {"state": "planned"}
        ),
        "agents": {
            "state": "unchanged" if plan["agents"]["exists"] else "planned"
        },
        "git_drivers": (
            {"state": "planned"} if plan["git"]["exists"] else {"state": "skipped", "reason": "not_git"}
        ),
    }
    return {
        "store": str(store),
        "pack": plan["store"]["pack"] or "basic",
        "stages": stages,
        "status": None,
        "approval": (
            "approved" if plan["approval"]["state"] == "recorded" else "missing"
        ),
        "next": _next_steps(plan["approval"]["state"] == "recorded"),
        "dry_run": True,
    }


def render_card(receipt: dict[str, Any]) -> str:
    """A short human-readable view of one onboarding receipt."""
    lines = [
        f"context rail - onboarding (store: {receipt['store']}, pack: {receipt['pack']})"
    ]
    for name, stage in receipt["stages"].items():
        state = stage.get("state", "unknown")
        detail = ""
        if stage.get("written"):
            detail = ": " + ", ".join(stage["written"])
        elif stage.get("reason"):
            detail = f" ({stage['reason']})"
        lines.append(f"  {name:<11} {state}{detail}")
    if receipt.get("status") is not None:
        counts = receipt["status"].get("counts", {})
        summary = ", ".join(f"{key} {value}" for key, value in counts.items())
        lines.append(f"status: {summary or '0 items'} - store approval: {receipt['approval']}")
    lines.append("next: " + " | ".join(receipt["next"]))
    return "\n".join(lines)


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
