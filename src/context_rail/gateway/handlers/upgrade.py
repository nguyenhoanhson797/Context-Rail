"""Bring a store written before the runtime-neutral engine up to date."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.delegation import (
    DELEGATION_USED,
    is_delegated,
    used_record,
)
from context_rail.gateway.delegation import (
    summary as delegation_summary,
)
from context_rail.gateway.execution import caller_session
from context_rail.gateway.handlers.adapters import SkillConflict, render_conflicts
from context_rail.gateway.handlers.shipped import (
    ShippedPlan,
    commit_shipped,
    plan_shipped,
    unedited_shipped_copies,
)
from context_rail.gateway.identity import name_is_human
from context_rail.model.errors import ModelError
from context_rail.store.doc import etag
from context_rail.store.lock import store_lock
from context_rail.store.upgrade import plan_upgrade
from context_rail.store.write import Op, WriteRefused, commit

if TYPE_CHECKING:  # the gateway imports this module only when upgrade is invoked
    from context_rail.gateway.gateway import Context

__all__ = ["upgrade"]

_SHIPPED_REASON = "upgrade shipped guidance"


def upgrade(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Rewrite profiles, local packs and the charter, then bring shipped guidance up to date."""
    delegated = is_delegated(ctx, "ops upgrade")
    attested = ctx.attestation is not None
    dry_run = bool(args.get("dry_run"))
    if not dry_run and not delegated and not attested and (
        not name_is_human(actor) or caller_session() is not None
    ):
        raise WriteRefused(
            "PERMISSION_DENIED",
            "upgrading a store is a human-only action",
            {"tool": "upgrade", "actor": actor},
        )
    if not dry_run and not delegated:
        reason = validate_confirmation("upgrade", "store", args.get("confirm"))
        if reason is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"target": "store", "confirm": confirmation_phrase("upgrade", "store")},
            )
    # A shipped copy the store never edited is replaced whole by the shipped step.
    # Stripping its removed keys first would make it look edited, so it is left alone here.
    replaced = unedited_shipped_copies(ctx.store, ctx.manifest.pack)
    changes = [
        change
        for change in plan_upgrade(ctx.store, ctx.manifest)
        if change.rel not in replaced
    ]
    changed = [change.rel for change in changes]
    reasons = {change.rel: list(change.reasons) for change in changes}
    summary: dict[str, Any] = {
        "changed": changed,
        "dry_run": dry_run,
        "reasons": reasons,
        "next": [],
        "record": None,
        "shipped": None,
    }
    if dry_run:
        plan = _preview_shipped(ctx.store, actor)
        if plan is not None:
            summary["shipped"] = plan.report()
        touched = plan.touched() if plan is not None else []
        needs_reapprove = bool(
            plan is not None and (plan.placements_added or plan.repair_hints)
        )
        summary["next"] = _next(
            ctx, [*changed, *touched], force_reapprove=needs_reapprove
        )
        summary["summary"] = _plain_summary(changed, plan, summary["next"], True)
        if delegated:
            summary["delegation"] = delegation_summary(ctx, "ops upgrade")
        return summary
    if changes:
        ops = [Op(change.rel, change.after, etag(change.before)) for change in changes]
        commit(ctx.store, ctx.manifest, ops, None, actor=actor, key=args.get("key"))
    plan = _write_shipped(ctx.store, actor, rewritten=bool(changes), changed=changed)
    if changes or plan.ops:
        record = {
            "kind": "upgrade.completed",
            "about": [],
            "data": {
                "schema": 1,
                "changed": changed,
                "reasons": reasons,
                "shipped": plan.report(),
                **({"attestation": ctx.attestation} if attested else {}),
            },
        }
        # Records are placed by the pack, which only loads once the files above
        # are rewritten. The index is opened only for the delegated audit record:
        # the repair path may have no pack to open it with.
        audit: dict[str, Any] = {}
        if delegated:
            audit = {
                "index": ctx.index,
                "extra_records": (used_record(ctx, "ops upgrade"),),
                "reserved_kinds": frozenset({DELEGATION_USED}),
            }
        summary["record"] = commit(
            ctx.store, ctx.manifest, [], record, actor=actor, **audit
        )["record"]
    summary["shipped"] = plan.report()
    needs_reapprove = bool(plan.placements_added or plan.repair_hints)
    summary["next"] = _next(
        ctx, [*changed, *plan.touched()], force_reapprove=needs_reapprove
    )
    summary["summary"] = _plain_summary(changed, plan, summary["next"], False)
    if attested:
        assert ctx.attestation is not None
        summary["attestation"] = ctx.attestation
        summary["summary"] += (
            f" Recorded {ctx.attestation['approved_by']}'s chat approval "
            "as unverified evidence."
        )
    if delegated:
        summary["delegation"] = delegation_summary(ctx, "ops upgrade")
    return summary


def _plain_summary(
    changed: list[str],
    plan: ShippedPlan | None,
    next_steps: list[str],
    dry_run: bool,
) -> str:
    """One sentence a reader can relay without reading the JSON lists."""
    writes = len(changed) + (len(plan.ops) if plan is not None else 0)
    edited = len(plan.customized) if plan is not None else 0
    if writes == 0 and edited == 0 and not next_steps:
        return "This store is already up to date."
    if writes == 0:
        text = (
            "No shipped files would change."
            if dry_run
            else "No shipped files changed."
        )
    else:
        verb = "would change" if dry_run else "changed"
        text = f"{writes} file{'' if writes == 1 else 's'} {verb}."
    if edited:
        text += f" {edited} edited file{'' if edited == 1 else 's'} left alone."
    if next_steps:
        text += " Next: " + "; ".join(next_steps) + "."
    return text


def _preview_shipped(store: Path, actor: str) -> ShippedPlan | None:
    """The shipped writes a real run would make, read without writing.

    None when the pack cannot load yet, because the rewrite has not run.
    """
    from context_rail.gateway.gateway import _open_context

    try:
        context = _open_context(store, actor)
    except (ModelError, WriteRefused):
        return None
    try:
        return plan_shipped(context, reason=_SHIPPED_REASON, seed=True)
    finally:
        context.close()


def _write_shipped(
    store: Path, actor: str, *, rewritten: bool, changed: list[str]
) -> ShippedPlan:
    """Plan and commit the shipped copies against the pack the rewrite left behind.

    Once the rewrite is committed, any failure here leaves the store half upgraded,
    so the refusal says so and names the re-run that finishes the upgrade. Before
    the rewrite, the failure is raised unchanged because nothing was written.
    """
    from context_rail.gateway.gateway import _open_context

    try:
        context = _open_context(store, actor)
        try:
            with store_lock(store):
                plan = plan_shipped(context, reason=_SHIPPED_REASON, seed=True)
                commit_shipped(context, plan, actor=actor)
        finally:
            context.close()
    except Exception as exc:
        if not rewritten:
            raise
        code = (
            exc.code
            if isinstance(exc, (WriteRefused, ModelError))
            else "INTEGRATION_FAILURE"
        )
        cause = exc.message if isinstance(exc, (WriteRefused, ModelError)) else str(exc)
        cause = (cause or exc.__class__.__name__).rstrip(".")
        raise WriteRefused(
            code,
            "the store files were upgraded, but the shipped guidance step failed: "
            f"{cause}. Fix that cause, then re-run `rail ops upgrade`; "
            "the re-run brings shipped profiles and skills up to date and writes the upgrade record.",
            {
                "upgraded": changed,
                "step": "shipped guidance",
                "cause_code": code,
                "rerun": "rail ops upgrade",
            },
        ) from exc
    return plan


def _next(
    ctx: Context, changed: list[str], *, force_reapprove: bool = False
) -> list[str]:
    if not changed and not force_reapprove:
        return []
    steps: list[str] = []
    if force_reapprove or _approval_changed(ctx):
        steps.append(
            "rail approve charter --confirmer <name> --confirm "
            + repr(confirmation_phrase("approve", "charter"))
        )
    if changed:
        conflicts = _render_conflicts(ctx)
        if conflicts is None:
            return steps
        if conflicts:
            steps.extend(_conflict_step(conflict) for conflict in conflicts)
        else:
            steps.append("rail ops adapters render")
    return steps


def _render_conflicts(ctx: Context) -> list[SkillConflict] | None:
    """The skill conflicts a render would refuse, or None when unreadable."""
    from context_rail.gateway.gateway import _open_context

    try:
        fresh = _open_context(ctx.store, ctx.actor)
    except (ModelError, WriteRefused, OSError):
        return None
    try:
        return render_conflicts(fresh)
    except (ModelError, WriteRefused, OSError):
        return None
    finally:
        fresh.close()


def _conflict_step(conflict: SkillConflict) -> str:
    paths = ", ".join(conflict.paths)
    return (
        f"skill {conflict.name!r} is exported from two sources: {paths}; "
        "rename or remove one, run `rail ops reindex`, then `rail ops adapters render`"
    )


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
