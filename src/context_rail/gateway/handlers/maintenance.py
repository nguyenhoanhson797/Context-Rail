"""Maintenance tools: reindex, and the ``ci`` gate over indexed state."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from context_rail.doctor.checks import gate_report, run_doctor
from context_rail.store.index import Index
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["ci", "reindex"]


def reindex(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Sync the index, or rebuild it with ``--full``, and report the counts."""
    index = Index.open(ctx.store, ctx.pack)
    try:
        stats = index.rebuild() if args["full"] else index.sync()
    finally:
        index.close()
    return asdict(stats)


def ci(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Refuse the store when the doctor finds an error or a done Item's gate is shut.

    The report is read only: it evaluates gates from indexed checks and reviews
    and never runs a Check, launches a droid or writes a Record, so a failing
    pipeline costs no credits. ``--warnings-as-errors`` turns every warning into
    a failure; without it warnings are counted and reported.
    """
    report = run_doctor(ctx.store, ctx.manifest, ctx.pack, ctx.index)
    gates = gate_report(ctx.store, ctx.manifest, ctx.pack, ctx.index)
    counts = dict(report["counts"])
    issues = list(report["issues"])
    failed = counts["error"] > 0 or (
        bool(args["warnings_as_errors"]) and counts["warning"] > 0
    )
    if failed:
        raise WriteRefused(
            "BUSINESS_RULE_VIOLATED",
            f"ci found {counts['error']} error(s) and {counts['warning']} warning(s)",
            {"counts": counts, "issues": issues, "gates": gates},
        )
    return {"passed": True, "issues": issues, "counts": counts, "gates": gates}
