"""Charter and profile budgets, the run facts they are measured against, and the
status moves a dispatch, review or merge performs on an Item."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from context_rail.gateway.handlers.common import read_doc
from context_rail.gateway.handlers.docs import move as move_item
from context_rail.kernel import structure
from context_rail.kernel.budgets import AttemptUsage, Budget, RunFacts
from context_rail.kernel.lifecycle import category_of
from context_rail.store import query
from context_rail.store.doc import etag
from context_rail.store.write import WriteRefused, resolve_unique

from .common import BUILTIN_SECONDS, IMPL_ROLE

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _effective_budget(
    charter: dict[str, Any], profile: dict[str, Any], retries: int
) -> Budget:
    """The stricter non-null bound of the charter, the profile and the pack.

    ``retries`` is the pack's ceiling on implementation attempts: the first
    attempt plus that many retries, whatever the charter or profile allow.
    """
    cap = int(retries) + 1
    declared = [
        int(value)
        for value in (charter.get("attempts"), profile.get("attempts"))
        if value is not None
    ]
    return Budget(
        attempts=min(declared + [cap]),
        seconds=float(_stricter(charter.get("seconds"), profile.get("seconds"), BUILTIN_SECONDS)),
        tokens=_stricter(charter.get("tokens"), profile.get("tokens"), None),
        credits=_stricter(charter.get("credits"), profile.get("credits"), None),
    )


def _stricter(first: Any, second: Any, default: Any) -> Any:
    values = [value for value in (first, second) if value is not None]
    return min(values) if values else default


def _run_facts(index: Any, slug: str) -> tuple[list[RunFacts], float, int]:
    """The item's run facts, the seconds every role used, and implementer attempts.

    The attempt count excludes reviews so it agrees with
    :func:`context_rail.kernel.budgets.launch_refusal`; the elapsed seconds keep
    counting both worker roles, because review time is spent time.
    """
    facts: list[RunFacts] = []
    used = 0.0
    used_attempts = 0
    for row in query.run_facts(index, slug):
        used += float(row["seconds"])
        if row["role"] == IMPL_ROLE:
            used_attempts += int(row["attempts"])
        facts.append(
            RunFacts(
                item=slug,
                role=row["role"],
                attempts=row["attempts"],
                seconds=float(row["seconds"]),
                usage=tuple(
                    AttemptUsage(
                        input_tokens=usage.get("input_tokens", "unknown"),
                        output_tokens=usage.get("output_tokens", "unknown"),
                        factory_credits=usage.get("factory_credits", "unknown"),
                    )
                    for usage in row["usage"]
                ),
                run_id=row["run"],
            )
        )
    return facts, used, used_attempts


def _require_executable(ctx: Context, slug: str) -> None:
    if slug in structure.executable(_item_rows(ctx)):
        return
    reason = structure.blocked(_item_rows(ctx)).get(slug) or "not executable"
    raise WriteRefused(
        "INVALID_STATE",
        f"{slug!r} cannot be dispatched: {reason}",
        {"item": slug, "reason": reason},
    )


def _item_rows(ctx: Context) -> list[structure.ItemRow]:
    index = ctx.index
    edges: dict[str, dict[str, list[str]]] = {}
    for src, rel, dst in index.conn.execute(
        "SELECT r.src, r.rel, r.dst FROM relations r JOIN docs d ON d.path = r.src_path "
        "WHERE d.primitive = 'item' AND d.broken = 0 "
        "AND r.rel IN ('part_of', 'depends_on')"
    ):
        edges.setdefault(src, {}).setdefault(rel, []).append(dst)
    rows = index.conn.execute(
        "SELECT slug, category, status, preset FROM docs "
        "WHERE primitive = 'item' AND broken = 0"
    ).fetchall()
    result: list[structure.ItemRow] = []
    for name, category, status, preset in rows:
        declared = ctx.pack.presets.get(preset or "")
        workflow = ctx.pack.workflows[
            declared.workflow if declared is not None else "default"
        ]
        result.append(
            structure.ItemRow(
                slug=name,
                category=category,
                part_of=tuple(sorted(edges.get(name, {}).get("part_of", ()))),
                depends_on=tuple(sorted(edges.get(name, {}).get("depends_on", ()))),
                last_activity_ms=None,
                dispatchable=workflow.dispatchable(status or ""),
                preset=preset or None,
                workflow=workflow.name,
                container=declared.container if declared is not None else None,
            )
        )
    return result


def _move_active(ctx: Context, slug: str, actor: str) -> None:
    rel = resolve_unique(ctx.index, slug)
    data, doc = read_doc(ctx.store, rel)
    if category_of(ctx.pack, doc.status or "") == "active":
        return
    target = _status_in(ctx.pack.statuses, "active")
    move_item(
        ctx,
        {
            "slug": slug,
            "status": target,
            "expect": etag(data),
            "resolution": None,
            "confirm": None,
            "key": None,
        },
        actor,
    )


def _move_blocked(ctx: Context, slug: str, actor: str) -> None:
    rel = resolve_unique(ctx.index, slug)
    data, doc = read_doc(ctx.store, rel)
    if category_of(ctx.pack, doc.status or "") == "blocked":
        return
    target = _status_in(ctx.pack.statuses, "blocked")
    move_item(
        ctx,
        {
            "slug": slug,
            "status": target,
            "expect": etag(data),
            "resolution": None,
            "confirm": None,
            "key": None,
        },
        actor,
    )


def _status_in(statuses: dict[str, str], category: str) -> str:
    for status, found in statuses.items():
        if found == category:
            return status
    raise WriteRefused(
        "VALIDATION_FAILED",
        f"the pack declares no {category!r} status",
        {"category": category},
    )
