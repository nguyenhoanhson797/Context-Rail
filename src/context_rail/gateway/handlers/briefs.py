"""``rail loop brief``: the context an agent in any tool works an item from."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.context.work_brief import (
    DEFAULT_CHARS,
    NAME_MODE_ROLES,
    build_work_brief,
    reviews_work,
)
from context_rail.gateway.handlers.common import checkout, read_refusal
from context_rail.runner import git
from context_rail.store import query
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["brief"]

_MAX_CHARS = (100, 32000)


def brief(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """The work brief for one item, read only; it records and moves nothing."""
    item = args.get("item")
    if not isinstance(item, str) or not item.strip():
        raise WriteRefused("VALIDATION_FAILED", "a brief needs an item", {"param": "item"})
    item = item.strip()
    limit = _limit(args.get("max_chars"))
    ctx.index.sync()
    row = ctx.index.conn.execute(
        "SELECT primitive FROM docs WHERE slug = ? ORDER BY path LIMIT 1", (item,)
    ).fetchone()
    if row is None or row[0] != "item":
        raise WriteRefused("NOT_FOUND", f"no item {item!r}", {"item": item})
    refusal = read_refusal(ctx, item)
    if refusal is not None:
        raise refusal
    role = _role(ctx, args.get("role"))
    candidate = _candidate(ctx, item, args.get("commit")) if reviews_work(ctx, role) else None
    return build_work_brief(
        ctx,
        item=item,
        role=role,
        commit=args.get("commit"),
        max_chars=limit,
        candidate=candidate,
        granted_by=ctx.identity.name,
    )


def _limit(value: Any) -> int | None:
    if value is None:
        return None
    low, high = _MAX_CHARS
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"max_chars must be a number from {low} to {high}; it only lowers "
            f"the default of {DEFAULT_CHARS}",
            {"param": "max_chars"},
        )
    return value


def _role(ctx: Context, requested: Any) -> str:
    if requested is not None and (not isinstance(requested, str) or not requested.strip()):
        raise WriteRefused("VALIDATION_FAILED", "role must be a name", {"param": "role"})
    wanted = requested.strip() if isinstance(requested, str) else None
    if not ctx.role_mode:
        role = wanted or "implementer"
        if role not in NAME_MODE_ROLES:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"role must be one of {', '.join(NAME_MODE_ROLES)} in a store without roles",
                {"param": "role", "candidates": list(NAME_MODE_ROLES)},
            )
        return role
    roles = ctx.pack.operating.roles
    identity = ctx.identity
    own = identity.role.name if identity.role is not None else None
    role = wanted or own
    if role is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "name the role to read the brief as: --role <role>",
            {"param": "role", "candidates": sorted(roles)},
        )
    if role not in roles:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the pack declares no role {role!r}",
            {"param": "role", "candidates": sorted(roles)},
        )
    if identity.human or role == own:
        return role
    grantable = (
        list(identity.role.can.methods.get("grant") or ()) if identity.role is not None else []
    )
    if role not in grantable:
        raise WriteRefused(
            "PERMISSION_DENIED",
            f"{identity.name!r} may not read the brief of role {role!r}",
            {
                "reason": "brief_role_not_grantable",
                "role": role,
                "own_role": own,
                "can_grant": grantable,
            },
        )
    return role


def _candidate(ctx: Context, item: str, rev: Any) -> dict[str, Any]:
    if rev is None:
        found = query.latest_candidate(ctx.index, item)
    else:
        root, _git_dir = checkout(ctx, action="reading a candidate")
        sha = git.resolve_commit(root, str(rev))
        if sha is None:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rev!r} names no commit", {"param": "commit"}
            )
        found = query.candidate_for_commit(ctx.index, item, sha)
    if found is None:
        raise WriteRefused(
            "INVALID_STATE",
            f"no candidate is recorded for {item!r}; the implementer records one with "
            f"`rail loop candidate {item} --commit <sha>`",
            {"reason": "no_candidate", "item": item},
        )
    return found