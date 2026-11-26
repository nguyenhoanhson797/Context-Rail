"""A human's chat approval, kept as evidence rather than a signature.

The engine can prove which agent wrote a record; it cannot prove who typed the
words in a conversation it never saw. A record therefore keeps the human's name,
their exact words and the agent that relayed them, and marks the claim
unverified. Refusing a non-false ``verified`` flag keeps a later writer from
upgrading the claim by accident.
"""

from __future__ import annotations

from typing import Any

from context_rail.model.fields import (
    boolean,
    mapping,
    no_unknown,
    refuse,
    require,
    schema_version,
    string,
)

__all__ = [
    "ATTESTATION_SCHEMA",
    "ATTESTATION_TEXT_LIMIT",
    "parse_attestation",
]

ATTESTATION_SCHEMA = 1
#: An approval is a sentence or a short paragraph, not a transcript.
ATTESTATION_TEXT_LIMIT = 4000
_KEYS = (
    "schema",
    "approved_by",
    "text",
    "recorded_by",
    "channel",
    "verified",
)


def parse_attestation(value: Any) -> dict[str, Any]:
    """Validate one chat attestation, refusing every unknown or upgraded field."""
    data = mapping(value, "attestation")
    no_unknown(data, _KEYS, "attestation")
    require(data, _KEYS, "attestation")
    schema = schema_version(data, ATTESTATION_SCHEMA, "attestation")
    approved_by = string(data["approved_by"], "attestation.approved_by")
    text = string(data["text"], "attestation.text")
    if len(text) > ATTESTATION_TEXT_LIMIT:
        refuse(
            f"attestation.text is longer than {ATTESTATION_TEXT_LIMIT} characters; "
            "quote only what the human said"
        )
    recorded_by = string(data["recorded_by"], "attestation.recorded_by")
    channel = string(data["channel"], "attestation.channel")
    if channel != "chat":
        refuse("attestation.channel must be 'chat'")
    verified = boolean(data["verified"], "attestation.verified")
    if verified:
        refuse("attestation.verified must be false; chat words are not a signature")
    return {
        "schema": schema,
        "approved_by": approved_by,
        "text": text,
        "recorded_by": recorded_by,
        "channel": channel,
        "verified": verified,
    }
