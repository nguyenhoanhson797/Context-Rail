"""Set the charter's delegation through the store's one write path.

The human names one lead and the operational actions it may run. The edit is a
charter change, so it needs approval before the delegation becomes live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from context_rail.core.util import dump_frontmatter, load_yaml, now_iso
from context_rail.gateway.confirmation import (
    confirmation_phrase,
    validate_confirmation,
)
from context_rail.model.delegation import parse_delegation
from context_rail.store.approval import charter_document
from context_rail.store.doc import etag
from context_rail.store.write import Op, WriteRefused, commit, replay

if TYPE_CHECKING:  # imported lazily by the gateway only when delegate runs
    from context_rail.gateway.gateway import Context

__all__ = ["delegate"]

_APPROVE_NEXT = [
    'rail approve charter --confirm "APPROVE charter" --confirmer "<you>"'
]


def delegate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Set or remove the charter's delegation block as the human names it."""
    if not ctx.identity.human:
        raise WriteRefused(
            "PERMISSION_DENIED",
            "setting a delegation is a human-only action",
            {"tool": "delegate", "actor": actor},
        )
    found = charter_document(ctx)
    if found is None:
        raise WriteRefused(
            "INVALID_STATE",
            "this store has no charter to delegate from",
            {"reason": "charter_missing"},
        )
    path, _ = found
    to = args["to"]
    remove = args.get("remove") is True
    reason = validate_confirmation("delegate", to, args.get("confirm"))
    if reason is not None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            reason,
            {
                "target": to,
                "confirm": confirmation_phrase("delegate", to),
            },
        )

    target = ctx.store / path
    raw = target.read_bytes()
    actual = etag(raw)
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{path} has no frontmatter to edit",
            {"path": path},
        )
    end = text.find("\n---\n", 4)
    if end < 0:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{path} has an unterminated frontmatter block",
            {"path": path},
        )
    fields = load_yaml(text[4:end])
    if not isinstance(fields, dict):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{path} frontmatter is not a mapping",
            {"path": path},
        )
    if replay(ctx.store, args.get("key")) is not None:
        current = fields.get("delegation")
        return {
            "charter": path,
            "etag": actual,
            "delegation": current if isinstance(current, dict) else None,
            "next": [] if ctx.approval["state"] == "approved" else list(_APPROVE_NEXT),
        }
    if args["expect"] != actual:
        raise WriteRefused(
            "CONFLICT",
            f"{path} is at {actual}, not {args['expect']}",
            {"expected": args["expect"], "actual": actual},
        )
    if remove:
        current = fields.get("delegation")
        if not isinstance(current, dict):
            return {"charter": path, "etag": actual, "delegation": None, "next": []}
        current_to = current.get("to")
        if current_to not in (None, to):
            raise WriteRefused(
                "CONFLICT",
                f"the delegation names {current_to!r}, not {to!r}",
                {"expected": current_to, "actual": to},
            )
        fields.pop("delegation", None)
        value = None
    else:
        requested = list(args.get("action") or ())
        block: dict[str, Any] = {"to": to}
        if requested:
            block["actions"] = requested
        delegation = parse_delegation(block)
        fields["delegation"] = dict(block)
        value = {"to": delegation.to}
        if requested:
            value["actions"] = list(delegation.actions)
    fields["updated"] = now_iso()
    updated = ("---\n" + dump_frontmatter(fields) + "---\n" + text[end + 5 :]).encode(
        "utf-8"
    )
    commit(
        ctx.store,
        ctx.manifest,
        [Op(path, updated, actual)],
        None,
        actor=actor,
        key=args.get("key"),
        index=ctx.index,
    )
    return {
        "charter": path,
        "etag": etag(updated),
        "delegation": value,
        "next": list(_APPROVE_NEXT),
    }
