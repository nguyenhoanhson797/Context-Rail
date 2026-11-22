"""Record one human's explicit consent for an item, in the human's own words.

The record proves who recorded it and what was said; it cannot prove who spoke.
That is why the text is kept verbatim and the agent that called is named as the
signer, while the human who gave it is named as the party it was given for.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.gateway.handlers.common import record_data, signing_channel
from context_rail.model.consent import (
    CONSENT_KIND,
    CONSENT_SCHEMA,
    CONSENT_TEXT_LIMIT,
)
from context_rail.store.approval import semantic_hash
from context_rail.store.doc import etag as etag_of
from context_rail.store.lock import store_lock
from context_rail.store.write import (
    WriteRefused,
    commit,
    replay,
    resolve_unique,
)

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["consent"]


def consent(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Record the human's words for the item version they saw."""
    item = (args.get("item") or "").strip()
    if not item:
        raise WriteRefused(
            "VALIDATION_FAILED", "a consent needs an item", {"argument": "item"}
        )
    on_behalf_of = (args.get("on_behalf_of") or "").strip()
    if not on_behalf_of:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a consent needs the human it was given by",
            {"argument": "on_behalf_of"},
        )
    text = (args.get("text") or "").strip()
    if not text:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a consent needs the human's own words",
            {"argument": "text"},
        )
    if len(text) > CONSENT_TEXT_LIMIT:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the consent text must be at most {CONSENT_TEXT_LIMIT} characters",
            {"argument": "text"},
        )
    expect = (args.get("expect") or "").strip()
    if not expect:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a consent needs the item etag the human saw",
            {"argument": "expect"},
        )

    store = ctx.store
    with store_lock(store):
        index = ctx.index
        if args.get("key") is not None:
            saved = replay(store, args["key"])
            if saved is not None:
                record = saved.get("record")
                return _summary(record_data(index, record), record)
        path = resolve_unique(index, item)
        row = index.conn.execute(
            "SELECT slug, primitive FROM docs WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            raise WriteRefused("NOT_FOUND", f"no item {item!r}", {"item": item})
        slug, primitive = row
        if primitive != "item":
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{item!r} is a {primitive}, not an item",
                {"item": item, "primitive": primitive},
            )
        raw = (Path(store) / path).read_bytes()
        etag = etag_of(raw)
        if etag != expect:
            raise WriteRefused(
                "CONFLICT",
                f"{item!r} is at {etag}, not {expect}; show it again before recording consent",
                {"expected": expect, "actual": etag},
            )
        digest = semantic_hash(raw, format="markdown")
        signer_kind = "human" if ctx.identity.human else "agent"
        data = {
            "schema": CONSENT_SCHEMA,
            "item": slug,
            "item_etag": etag,
            "item_digest": digest,
            "text": text,
            "on_behalf_of": on_behalf_of,
            "signer": actor,
            "signer_kind": signer_kind,
            "channel": signing_channel(),
        }
        result = commit(
            store,
            ctx.manifest,
            [],
            {
                "preset": "event",
                "kind": CONSENT_KIND,
                "about": [slug],
                "data": data,
            },
            actor=actor,
            key=args.get("key"),
            index=index,
            reserved_kinds=frozenset({CONSENT_KIND}),
        )
    return _summary(data, result.get("record"))


def _summary(data: dict[str, Any], record: Any = None) -> dict[str, Any]:
    return {
        "consent": record or data.get("id"),
        "item": data.get("item"),
        "item_etag": data.get("item_etag"),
        "item_digest": data.get("item_digest"),
        "on_behalf_of": data.get("on_behalf_of"),
        "signer": data.get("signer"),
        "signer_kind": data.get("signer_kind"),
        "channel": data.get("channel"),
        "text": data.get("text"),
    }
