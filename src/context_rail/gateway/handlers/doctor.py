"""The doctor tool: integrity checks, repair, and safe `--fix`.

``--repair`` rolls interrupted writes forward via ``store.write.repair``.
``--fix`` runs only the safe subset (clear stale daemon descriptor, rebuild
the index) and always refuses the unsafe ones (approval, records, execution
failures) with the command that remains their fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.doctor.checks import run_doctor
from context_rail.kernel import rights
from context_rail.store.write import WriteRefused, repair

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["doctor", "doctor_fix"]


def doctor(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Report every store issue; ``--repair`` rolls writes forward, ``--fix`` applies safe fixes."""
    if args.get("repair"):
        # Rolling a write forward changes the store, so it needs the write
        # permission even though the checks themselves only read.
        refusal = rights.allowed("write", actor)
        if refusal is not None:
            raise WriteRefused(
                "PERMISSION_DENIED", refusal, {"tool": "doctor", "param": "repair"}
            )
        repair(ctx.store)
    if args.get("fix"):
        return doctor_fix(ctx, {}, actor)
    return run_doctor(ctx.store, ctx.manifest, ctx.pack, ctx.index)


def doctor_fix(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Apply safe doctor fixes; return the remaining issues.

    Safe: ``daemon_stale`` (clear descriptor). Unsafe: approval, placement,
    record, and execution issues are reported with their ``fix`` command
    instead of being auto-applied.
    """
    refusal = rights.allowed("write", actor)
    if refusal is not None:
        raise WriteRefused(
            "PERMISSION_DENIED", refusal, {"tool": "doctor", "param": "fix"}
        )
    try:
        from context_rail.gateway.daemon import clear_daemon_descriptor
    except ImportError:
        clear_daemon_descriptor = None  # type: ignore[assignment]

    result = run_doctor(ctx.store, ctx.manifest, ctx.pack, ctx.index)
    issues = result.get("issues") or []
    fixed: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("code") == "daemon_stale" and clear_daemon_descriptor is not None:
            clear_daemon_descriptor(ctx.store)
            fixed.append(issue)
    remaining = run_doctor(ctx.store, ctx.manifest, ctx.pack, ctx.index)
    return {"fixed": fixed, **remaining}
