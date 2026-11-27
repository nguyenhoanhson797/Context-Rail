"""Upgrade every store under one root, in place, with one approval.

``rail ops upgrade --all`` is a batch around the per-store upgrade. It plans
every store first, reports the ones that need a human, then upgrades only the
safe ones and renders their adapters. It never forces an edited file and never
approves a charter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from context_rail.core.locate import StoreLocationError, find_store, worktree_git_dir
from context_rail.core.result import fail, ok
from context_rail.gateway.gateway import invoke
from context_rail.store.write import WriteRefused

__all__ = ["invoke_all"]

_EXCLUDED = frozenset(
    {
        ".cache",
        ".git",
        ".hg",
        ".mypy_cache",
        ".next",
        ".nox",
        ".pytest_cache",
        ".rail-local",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "env",
        "htmlcov",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
        "venv",
    }
)
_RENDER = "rail ops adapters render"


def invoke_all(
    params: dict[str, Any], *, store: str | Path | None, actor: str | None
) -> dict[str, Any]:
    """Discover stores under the root, plan each, then upgrade the safe ones."""
    dry_run = bool(params.get("dry_run"))
    root = _root(params.get("root"), store)
    if not root.is_dir():
        return fail(
            "NOT_FOUND",
            f"root folder {str(root)!r} does not exist",
            {"root": str(root)},
        )
    targets = _discover(root)
    plans = [_plan(target, root, actor) for target in targets]
    if dry_run:
        return ok(_value(root, True, [_row(plan, dry_run=True) for plan in plans]))
    return ok(_value(root, False, [_apply(plan, params, actor) for plan in plans]))


def _plan(target: Path, root: Path, actor: str | None) -> dict[str, Any]:
    """The read-only upgrade and adapter plan for one store."""
    display = _display(target, root)
    planned = _call("upgrade", {"dry_run": True}, target, actor)
    adapters = None
    if planned.get("ok") is True and not _blockers(planned.get("value") or {}):
        adapters = _call(
            "adapters sync",
            {"dry_run": True, "force": False},
            target,
            actor,
        )
    return {
        "target": target,
        "path": display,
        "plan": planned,
        "adapters": adapters,
    }


def _apply(plan: dict[str, Any], params: dict[str, Any], actor: str | None) -> dict[str, Any]:
    """Apply one store's plan, unless the plan says a human must act first."""
    row = _row(plan, dry_run=True)
    if row["status"] != "needs a human" and row["writes"] == 0 and row["edited"] == 0:
        return {**row, "status": "up to date", "result": "up to date"}
    if row["status"] == "needs a human":
        return row

    target = plan["target"]
    upgraded = _call(
        "upgrade",
        {
            "dry_run": False,
            "confirm": params.get("confirm"),
            "approved_by": params.get("approved_by"),
            "user_approval": params.get("user_approval"),
            "key": params.get("key"),
        },
        target,
        actor,
    )
    if upgraded.get("ok") is not True:
        return _refusal(plan, upgraded, dry_run=False)

    blockers = _blockers(upgraded.get("value") or {})
    if blockers:
        return _row_from_value(plan, upgraded.get("value") or {}, dry_run=False, blockers=blockers)

    rendered = _call(
        "adapters sync",
        {
            "dry_run": False,
            "force": False,
            "approved_by": params.get("approved_by"),
            "user_approval": params.get("user_approval"),
        },
        target,
        actor,
    )
    value = upgraded.get("value") or {}
    writes, edited = _writes(value)
    if rendered.get("ok") is not True:
        return _refusal(plan, rendered, dry_run=False, writes=writes, edited=edited)
    writes += _adapter_writes(rendered.get("value") or {})
    return _row_from_counts(plan, writes, edited, dry_run=False, next_text="")


def _row(plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """The report row for a read-only plan."""
    planned = plan["plan"]
    if planned.get("ok") is not True:
        return _refusal(plan, planned, dry_run=dry_run)
    value = planned.get("value") or {}
    writes, edited = _writes(value)
    blockers = _blockers(value)
    if blockers:
        return _row_from_counts(
            plan,
            writes,
            edited,
            dry_run=dry_run,
            next_text="; ".join(blockers),
            status="needs a human",
        )
    adapters = plan.get("adapters")
    if adapters is not None:
        if adapters.get("ok") is not True:
            return _refusal(plan, adapters, dry_run=dry_run, writes=writes, edited=edited)
        adapter_value = adapters.get("value") or {}
        if adapter_value.get("refused"):
            return _row_from_counts(
                plan,
                writes,
                edited,
                dry_run=dry_run,
                next_text="repair the adapter files: "
                + ", ".join(adapter_value["refused"]),
                status="needs a human",
            )
        writes += _adapter_writes(adapter_value)
    return _row_from_counts(plan, writes, edited, dry_run=dry_run, next_text="")


def _row_from_value(
    plan: dict[str, Any],
    value: dict[str, Any],
    *,
    dry_run: bool,
    blockers: list[str],
) -> dict[str, Any]:
    writes, edited = _writes(value)
    return _row_from_counts(
        plan,
        writes,
        edited,
        dry_run=dry_run,
        next_text="; ".join(blockers),
        status="needs a human",
    )


def _row_from_counts(
    plan: dict[str, Any],
    writes: int,
    edited: int,
    *,
    dry_run: bool,
    next_text: str,
    status: str | None = None,
) -> dict[str, Any]:
    if status is None:
        status = _status(writes, dry_run)
    result = status
    if status == "needs a human" and writes:
        verb = "would change" if dry_run else "changed"
        result += f", {writes} file{'' if writes == 1 else 's'} {verb}"
    if edited:
        result += f", {edited} edited file{'' if edited == 1 else 's'} left alone"
    return {
        "store": plan["target"].name,
        "path": plan["path"],
        "status": status,
        "result": result,
        "next": next_text,
        "writes": writes,
        "edited": edited,
    }


def _refusal(
    plan: dict[str, Any],
    result: dict[str, Any],
    *,
    dry_run: bool,
    writes: int = 0,
    edited: int = 0,
) -> dict[str, Any]:
    error = result.get("error") or {}
    message = str(error.get("message") or "the store refused the caller")
    return _row_from_counts(
        plan,
        writes,
        edited,
        dry_run=dry_run,
        next_text=message,
        status="needs a human",
    )


def _status(writes: int, dry_run: bool) -> str:
    files = "file" if writes == 1 else "files"
    if dry_run:
        return f"{writes} {files} would change" if writes else "up to date"
    return f"upgraded, {writes} {files} changed" if writes else "up to date"


def _writes(value: dict[str, Any]) -> tuple[int, int]:
    """Upgrade writes and edited files the upgrade left alone."""
    shipped = value.get("shipped") or {}
    writes = len(value.get("changed") or [])
    for key in ("adopted", "removed", "seeded"):
        writes += len(shipped.get(key) or [])
    placements = shipped.get("placements") or {}
    writes += len(placements.get("added") or [])
    edited = len(shipped.get("customized") or [])
    edited += len(placements.get("customized") or [])
    return writes, edited


def _adapter_writes(value: dict[str, Any]) -> int:
    return len(value.get("written") or []) + len(value.get("removed") or [])


def _blockers(value: dict[str, Any]) -> list[str]:
    """Steps that need a human, never the adapter render this batch performs."""
    steps = [step for step in (value.get("next") or []) if step != _RENDER]
    shipped = value.get("shipped") or {}
    for hint in shipped.get("repair_hints") or []:
        message = hint.get("message") if isinstance(hint, dict) else None
        if message:
            steps.append(str(message))
    return list(dict.fromkeys(steps))


def _call(
    tool: str, args: dict[str, Any], target: Path, actor: str | None
) -> dict[str, Any]:
    """One store call; a broken store is reported, never allowed to stop the batch."""
    try:
        return invoke(tool, args, store=target, actor=actor)
    except WriteRefused as exc:
        return fail(exc.code, exc.message, exc.details)
    except Exception as exc:  # noqa: BLE001 - a corrupt store must not abort the batch
        return fail("INTERNAL", str(exc) or exc.__class__.__name__)


def _root(value: Any, store: str | Path | None) -> Path:
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser().resolve()
    if store is not None:
        return _project_root(find_store(Path(store)) or Path(store))
    found = find_store(Path.cwd())
    if found is not None:
        return _project_root(found)
    git = worktree_git_dir(Path.cwd())
    return (git[1] if git is not None else Path.cwd()).resolve()


def _project_root(store: Path) -> Path:
    try:
        git = worktree_git_dir(store)
    except (OSError, StoreLocationError):
        git = None
    if git is not None:
        return git[1].resolve()
    if store.name == "context-rail":
        return store.parent.resolve()
    return store.resolve()


def _discover(root: Path) -> list[Path]:
    """Every folder under ``root`` holding a rail.yaml, build trees excluded."""
    found: list[Path] = []

    def ignore(_error: OSError) -> None:
        return None

    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, onerror=ignore, followlinks=False
    ):
        dirnames[:] = sorted(name for name in dirnames if name not in _EXCLUDED)
        if "rail.yaml" in filenames:
            found.append(Path(dirpath).resolve())
    return sorted(set(found))


def _display(target: Path, root: Path) -> str:
    try:
        rel = target.relative_to(root)
    except ValueError:
        return target.as_posix()
    return rel.as_posix() or "."


def _value(root: Path, dry_run: bool, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "all": True,
        "root": str(root),
        "dry_run": dry_run,
        "stores": rows,
        "summary": _summary(root, rows, dry_run),
        "text": _table(root, rows),
    }


def _summary(root: Path, rows: list[dict[str, Any]], dry_run: bool) -> str:
    if not rows:
        return f"No stores found under {root}."
    up = sum(1 for row in rows if row["status"] == "up to date")
    needs = sum(1 for row in rows if row["status"] == "needs a human")
    changed = len(rows) - up - needs
    parts = [f"{len(rows)} store{'' if len(rows) == 1 else 's'}"]
    if up:
        parts.append(f"{up} up to date")
    if changed:
        parts.append(f"{changed} would change" if dry_run else f"{changed} upgraded")
    if needs:
        parts.append(f"{needs} need{'s' if needs == 1 else ''} a human")
    return ", ".join(parts) + f" under {root}."


def _table(root: Path, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"No stores found under {root}."
    headers = ("Store", "Path", "Result")
    widths = [
        max(len(headers[index]), *(len(row[key]) for row in rows))
        for index, key in enumerate(("store", "path", "result"))
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append(
            "  ".join(
                row[key].ljust(widths[index])
                for index, key in enumerate(("store", "path", "result"))
            ).rstrip()
        )
        if row.get("next"):
            lines.append("    next: " + row["next"])
    return "\n".join(lines)
