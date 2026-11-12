"""Bring a store written before the runtime-neutral engine up to date."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.execution import caller_session
from context_rail.gateway.identity import name_is_human
from context_rail.store.upgrade import plan_upgrade
from context_rail.model.errors import ModelError
from context_rail.store.doc import etag
from context_rail.store.write import Op, WriteRefused, commit

if TYPE_CHECKING:  # the gateway imports this module only when upgrade is invoked
    from context_rail.gateway.gateway import Context

__all__ = ["upgrade"]


def upgrade(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Rewrite profiles, local packs and the charter in place, then say what is next."""
    if not name_is_human(actor) or caller_session() is not None:
        raise WriteRefused(
            "PERMISSION_DENIED",
            "upgrading a store is a human-only action",
            {"tool": "upgrade", "actor": actor},
        )
    dry_run = bool(args.get("dry_run"))
    if not dry_run:
        reason = validate_confirmation("upgrade", "store", args.get("confirm"))
        if reason is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"target": "store", "confirm": confirmation_phrase("upgrade", "store")},
            )
    changes = plan_upgrade(ctx.store, ctx.manifest)
    changed = [change.rel for change in changes]
    summary: dict[str, Any] = {
        "changed": changed,
        "dry_run": dry_run,
        "reasons": {change.rel: list(change.reasons) for change in changes},
        "next": [],
        "record": None,
    }
    if dry_run:
        summary["next"] = _next(ctx, changed)
        return summary
    if changes:
        ops = [Op(change.rel, change.after, etag(change.before)) for change in changes]
        commit(ctx.store, ctx.manifest, ops, None, actor=actor, key=args.get("key"))
        record = {
            "kind": "upgrade.completed",
            "about": [],
            "data": {
                "schema": 1,
                "changed": changed,
                "reasons": {change.rel: list(change.reasons) for change in changes},
            },
        }
        # Records are placed by the pack, which only loads once the files above are rewritten.
        summary["record"] = commit(ctx.store, ctx.manifest, [], record, actor=actor)["record"]
    summary["next"] = _next(ctx, changed)
    return summary


def _next(ctx: Context, changed: list[str]) -> list[str]:
    if not changed:
        return []
    steps = ["rail ops adapters render"]
    if _approval_changed(ctx):
        steps.insert(
            0,
            "rail approve charter --confirmer <name> --confirm "
            + repr(confirmation_phrase("approve", "charter")),
        )
    return steps


def _approval_changed(ctx: Context) -> bool:
    from context_rail.gateway.gateway import _open_context

    try:
        fresh = _open_context(ctx.store, ctx.actor)
    except (ModelError, WriteRefused, OSError):
        return True
    try:
        return fresh.approval["state"] == "mismatch"
    finally:
        fresh.close()
