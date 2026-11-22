"""A human's recorded consent for one item, in the human's own words.

Consent is evidence, not a signature: it names the human who gave it, the agent
that recorded it and the exact text that was given. The engine can prove who
wrote the record, never who spoke the words, so the text is kept verbatim and
attributed instead of being paraphrased into a boolean.
"""

from __future__ import annotations

import re
from typing import Any

from context_rail.model.fields import (
    mapping,
    no_unknown,
    refuse,
    require,
    schema_version,
    string,
)

__all__ = [
    "CONSENT_KIND",
    "CONSENT_SCHEMA",
    "CONSENT_TEXT_LIMIT",
    "parse_consent",
]

CONSENT_KIND = "consent.recorded"
CONSENT_SCHEMA = 1
#: A consent is a sentence or a short paragraph, not a document.
CONSENT_TEXT_LIMIT = 4000
_CONSENT_KEYS = (
    "schema",
    "item",
    "item_etag",
    "item_digest",
    "text",
    "on_behalf_of",
    "signer",
    "signer_kind",
    "channel",
)
_ETAG_RE = re.compile(r"^[0-9a-f]{16}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SIGNER_KINDS = ("human", "agent")
_CHANNELS = ("terminal", "ui", "agent", "unknown")


def parse_consent(value: Any) -> dict[str, Any]:
    """Validate one consent record's data, refusing every unknown field."""
    data = mapping(value, "consent")
    no_unknown(data, _CONSENT_KEYS, "consent")
    require(data, _CONSENT_KEYS, "consent")
    schema = schema_version(data, CONSENT_SCHEMA, "consent")
    item = string(data["item"], "consent.item")
    if not _SLUG_RE.fullmatch(item):
        refuse(f"consent.item {item!r} is not a slug")
    item_etag = string(data["item_etag"], "consent.item_etag")
    if not _ETAG_RE.fullmatch(item_etag):
        refuse("consent.item_etag is not an etag")
    item_digest = string(data["item_digest"], "consent.item_digest")
    if not _DIGEST_RE.fullmatch(item_digest):
        refuse("consent.item_digest is not a semantic digest")
    text = string(data["text"], "consent.text")
    if len(text) > CONSENT_TEXT_LIMIT:
        refuse(
            f"consent.text is longer than {CONSENT_TEXT_LIMIT} characters; "
            "quote only what the human said"
        )
    on_behalf_of = string(data["on_behalf_of"], "consent.on_behalf_of")
    signer = string(data["signer"], "consent.signer")
    signer_kind = string(data["signer_kind"], "consent.signer_kind")
    if signer_kind not in _SIGNER_KINDS:
        refuse("consent.signer_kind must be 'human' or 'agent'")
    channel = string(data["channel"], "consent.channel")
    if channel not in _CHANNELS:
        refuse("consent.channel must be 'terminal', 'ui', 'agent' or 'unknown'")
    return {
        "schema": schema,
        "item": item,
        "item_etag": item_etag,
        "item_digest": item_digest,
        "text": text,
        "on_behalf_of": on_behalf_of,
        "signer": signer,
        "signer_kind": signer_kind,
        "channel": channel,
    }
