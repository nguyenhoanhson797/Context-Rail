"""Explicit verification and file-only freshness for ``mode: result`` Checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from context_rail.model.cto import RESULT_BINDING_KEYS, Check
from context_rail.store.results import select_result

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["result_freshness", "run_result_check"]


def run_result_check(
    ctx: Context, check_or_row: Check | Mapping[str, Any], target: str | None = None
) -> dict[str, Any]:
    """Verify one ``mode: result`` Check explicitly and return a verdict payload.

    Accepts either an indexed check-state mapping ``row`` or a ``Check``
    definition paired with ``target`` (item slug). Never resolves git, never
    launches a process, and never runs automatically when producer results are
    recorded.
    """
    from context_rail.gateway.handlers.checks import _payload

    if isinstance(check_or_row, Mapping):
        row = check_or_row
        item = target if target is not None else row.get("item")
    else:
        item = target if target is not None else check_or_row.item
        if not item and check_or_row.verifies:
            item = check_or_row.verifies[0]
        from context_rail.store import query

        row = {
            "slug": "",
            "item": item,
            "mode": check_or_row.mode,
            "definition_etag": "",
        }
        if isinstance(item, str) and item:
            matches = [
                candidate
                for candidate in query.check_states(ctx.index, item)
                if candidate.get("mode") == "result"
            ]
            if len(matches) == 1:
                row = matches[0]
    if not isinstance(item, str) or not item:
        return _payload(
            row,
            passed=False,
            reason="a result check needs an Item target",
            result=None,
        )
    selection = select_result(ctx, item)
    binding = selection.binding
    result_binding = (
        {
            "record": binding.record,
            "body_hash": binding.body_hash,
            "item_digest": binding.item_digest,
            "work_cycle": binding.work_cycle,
            "contract_digest": binding.contract_digest,
            "candidate_commit": binding.candidate_commit,
        }
        if binding is not None
        else None
    )
    evidence = [binding.record] if binding is not None else []
    candidate = binding.candidate_commit if binding is not None else None
    passed = selection.current and selection.completed and binding is not None
    reason = None if passed else (
        "; ".join(selection.reasons) or f"no current completed producer result for {item!r}"
    )
    return _payload(
        row,
        passed=passed,
        evidence=evidence,
        reason=reason,
        candidate_commit=candidate,
        result=result_binding,
    )


def result_freshness(ctx: Context, row: Mapping[str, Any]) -> str:
    """File-only freshness of a ``mode: result`` Check verdict; never touches git."""
    if row.get("verdict") is None:
        return "missing"
    if row.get("passed") is not True:
        return "stale"
    if row.get("check_etag") != row.get("definition_etag"):
        return "stale"
    binding = row.get("result_binding")
    if not isinstance(binding, Mapping):
        return "stale"
    if set(binding) != set(RESULT_BINDING_KEYS):
        return "stale"
    item = row.get("item")
    if not isinstance(item, str) or not item:
        return "stale"
    selection = select_result(ctx, item)
    if not selection.current or not selection.completed or selection.binding is None:
        return "stale"
    current = selection.binding
    if (
        binding.get("record") != current.record
        or binding.get("body_hash") != current.body_hash
        or binding.get("item_digest") != current.item_digest
        or binding.get("work_cycle") != current.work_cycle
        or binding.get("contract_digest") != current.contract_digest
        or binding.get("candidate_commit") != current.candidate_commit
    ):
        return "stale"
    return "fresh"
