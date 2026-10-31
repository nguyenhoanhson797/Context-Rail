"""Names every dispatch handler shares: role and verdict names, the limits a
bounded prompt or report obeys, the plan a launch is assembled into, and the
small helpers they all use."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.gateway.handlers import checks as checks_handler
from context_rail.gateway.handlers.common import read_doc
from context_rail.kernel.budgets import Budget
from context_rail.model.cto import Profile
from context_rail.model.errors import ModelError
from context_rail.model.primitives import ROLES
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner import merge as local_merge
from context_rail.runner.droid import DroidProfile
from context_rail.store.doc import etag
from context_rail.store.write import WriteRefused, resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


IMPL_ROLE = ROLES[1]
REVIEW_ROLE = ROLES[2]
REVIEW_VERDICTS = ("approve", "reject")
REVIEW_DIFF_MAX = 12000
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
BUILTIN_ATTEMPTS = 2
BUILTIN_SECONDS = 3600.0
CAPTURE_REASON = "the droid output exceeded the 262144-byte cap"
SUMMARY_MAX = 2000
ADAPTER_PATHS = ("AGENTS.md", ".agents")
CHECKOUT_MESSAGE = "chore: checkpoint the store before a dispatch"
MERGE_CHECKPOINT_MESSAGE = "chore: checkpoint the store before a merge"
CLOSEOUT_MESSAGE = "chore: record the merge closeout"


def effective_attempts(budget: Budget) -> int:
    """The attempt cap, with the shared default when the budget leaves it open."""
    return max(1, int(budget.attempts if budget.attempts is not None else BUILTIN_ATTEMPTS))


def effective_seconds(budget: Budget) -> float:
    """The time cap, with the shared default when the budget leaves it open."""
    return float(budget.seconds if budget.seconds is not None else BUILTIN_SECONDS)


@dataclass
class _Plan:
    """Everything one launch needs, assembled before the droid can start."""

    ctx: Context
    store: Path
    actor: str
    slug: str
    expect: str
    profile_name: str
    profile: Profile
    profile_etag: str
    droid: DroidProfile
    budget: Budget
    dispatch_id: str
    paths: local_dispatch.DispatchPaths
    base: str
    store_paths: tuple[str, ...]
    checks: tuple[str, ...]
    skill_pins: list[dict[str, Any]]
    used_seconds: float
    max_attempts: int


def _optional(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any, what: str) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        return None
    return [entry.strip() for entry in value]


def _capture_payload(capture: dict[str, Any], output_omitted: int) -> dict[str, Any]:
    """The capture object, incomplete when either list or output was cut."""
    omitted = int(capture.get("omitted") or 0) + int(output_omitted or 0)
    reason = CAPTURE_REASON if output_omitted else capture.get("reason")
    return {
        "complete": bool(capture.get("complete")) and omitted == 0,
        "reason": reason,
        "omitted": omitted,
    }


def _resolve_item(ctx: Context, item: str) -> tuple[str, str, str]:
    rel = resolve_unique(ctx.index, item)
    data, doc = read_doc(ctx.store, rel)
    if doc.primitive != "item":
        raise WriteRefused(
            "VALIDATION_FAILED", f"{rel!r} is not an Item", {"path": rel}
        )
    return doc.slug, rel, etag(data)


def _refuse_pending(store: Path, slug: str) -> None:
    pending = local_dispatch.pending_dispatches(store, slug)
    if pending:
        entry = pending[0]
        raise WriteRefused(
            "CONFLICT",
            "a dispatch for this Item is already pending; inspect it before retrying",
            {
                "dispatch_id": entry.get("dispatch_id"),
                "phase": entry.get("phase", "unknown"),
                "worktree": entry.get("worktree"),
                "branch": entry.get("branch"),
            },
        )
    merges = local_merge.pending_merges(store, slug)
    if merges:
        entry = merges[0]
        raise WriteRefused(
            "CONFLICT",
            "a merge for this Item is already pending; inspect it before retrying",
            {
                "merge_id": entry.get("merge_id"),
                "phase": entry.get("phase", "unknown"),
            },
        )


def _verify_worktree(
    ctx: Context, slug: str, worktree: Path, actor: str
) -> dict[str, Any]:
    """Verify the Item's executable Checks against one checkout."""
    try:
        return checks_handler.verify(
            ctx,
            {
                "target": slug,
                "worktree": str(worktree),
                "verdict": None,
                "evidence": [],
                "confirm": None,
                "key": None,
            },
            actor,
        )
    except (WriteRefused, ModelError) as error:
        return {"passed": False, "verdicts": [], "error": error.message}
