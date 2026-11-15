"""``rail loop brief``: the context an agent in any tool works an item from."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.context import results as result_context_mod
from context_rail.context.work_brief import (
    DEFAULT_CHARS,
    NAME_MODE_ROLES,
    build_work_brief,
    reviews_work,
)
from context_rail.gateway.handlers.common import read_refusal
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
    compiled = result_context_mod.compiled_results_for_item(ctx, item)
    reviewing = reviews_work(ctx, role)
    candidate: dict[str, Any] | None = None
    result_info: dict[str, Any] | None = None
    if reviewing:
        candidate, result_info = _review_target(
            ctx, item, args.get("commit"), contract_enabled=compiled is not None
        )
    elif compiled is not None:
        result_info = result_context_mod.result_context(ctx, item, include_body=False)
    return build_work_brief(
        ctx,
        item=item,
        role=role,
        commit=args.get("commit"),
        max_chars=limit,
        candidate=candidate,
        result_info=result_info,
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


def _review_target(
    ctx: Context, item: str, rev: Any, *, contract_enabled: bool
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate: dict[str, Any] | None
    if rev is None:
        candidate = query.latest_candidate(ctx.index, item)
    else:
        needle = str(rev).strip()
        candidate = _indexed_candidate(ctx, item, needle)
        if candidate is None:
            if not contract_enabled:
                raise WriteRefused(
                    "INVALID_STATE",
                    f"no candidate is recorded for {item!r}; the implementer records one with "
                    f"`rail loop candidate {item} --commit <sha>`",
                    {"reason": "no_candidate", "item": item},
                )
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rev!r} names no recorded candidate commit", {"param": "commit"}
            )

    if not contract_enabled:
        if candidate is None:
            raise WriteRefused(
                "INVALID_STATE",
                f"no candidate is recorded for {item!r}; the implementer records one with "
                f"`rail loop candidate {item} --commit <sha>`",
                {"reason": "no_candidate", "item": item},
            )
        return candidate, None

    result_info = result_context_mod.result_context(
        ctx,
        item,
        include_body=True,
        candidate_commit=candidate["commit"] if candidate is not None else None,
    )
    if candidate is None and result_info.get("selected") is None:
        raise WriteRefused(
            "INVALID_STATE",
            f"no result or candidate is recorded for {item!r}; record a result with "
            f"`rail loop result {item} --body-file <file>`",
            {"reason": "no_result_or_candidate", "item": item},
        )
    return candidate, result_info


def _indexed_candidate(ctx: Context, item: str, needle: str) -> dict[str, Any] | None:
    if not needle or needle.startswith("-") or any(ch.isspace() for ch in needle):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{needle!r} names no commit", {"param": "commit"}
        )
    if len(needle) == 40:
        return query.candidate_for_commit(ctx.index, item, needle)
    candidates = query.candidates_for(ctx.index, item, limit=100)
    matches = [
        row
        for row in candidates
        if row["commit"] == needle
        or (len(needle) >= 4 and row["commit"].startswith(needle))
    ]
    if not matches:
        return None
    unique_shas = sorted({row["commit"] for row in matches})
    if len(unique_shas) > 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"commit prefix {needle!r} matches multiple recorded candidates for {item!r}",
            {"param": "commit", "value": needle, "matches": unique_shas},
        )
    return query.candidate_for_commit(ctx.index, item, unique_shas[0])