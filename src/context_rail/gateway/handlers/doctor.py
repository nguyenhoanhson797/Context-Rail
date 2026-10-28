"""The doctor tool: the integrity checks, with an optional repair pass first."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.doctor.checks import run_doctor
from context_rail.kernel import rights
from context_rail.store.write import WriteRefused, repair

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["doctor"]


def doctor(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Report every store issue; ``--repair`` first rolls interrupted writes forward."""
    if args["repair"]:
        # Rolling a write forward changes the store, so it needs the write
        # permission even though the checks themselves only read.
        refusal = rights.allowed("write", actor)
        if refusal is not None:
            raise WriteRefused(
                "PERMISSION_DENIED", refusal, {"tool": "doctor", "param": "repair"}
            )
        repair(ctx.store)
    return run_doctor(ctx.store, ctx.manifest, ctx.pack, ctx.index)
