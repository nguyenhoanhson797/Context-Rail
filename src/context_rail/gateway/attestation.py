"""A human's chat approval, recorded as evidence for one operational action.

The engine never sees the conversation. An agent that has shown the plan may
pass the human's exact words here; the record names the human, the words and
the agent, and marks the claim unverified. This is a soft gate: it makes an
agent ask before acting, not proof that a person clicked.
"""

from __future__ import annotations

from typing import Any, Mapping

from context_rail.model.attestation import (
    ATTESTATION_SCHEMA,
    parse_attestation,
)
from context_rail.store.write import WriteRefused

__all__ = ["ATTESTED_TOOLS", "attestation"]

#: The only tools a chat attestation may authorize.
ATTESTED_TOOLS = frozenset({"upgrade", "adapters sync", "approve"})


def attestation(
    tool_name: str, params: Mapping[str, Any], *, attested_by: str | None = None
) -> dict[str, Any] | None:
    """The attestation this call carries, or ``None`` when it carries none.

    ``upgrade`` and ``adapters sync`` need both ``approved_by`` and
    ``user_approval``. ``approve`` already requires ``confirmer``, so its
    ``user_approval`` is the only extra field. A partial pair is refused rather
    than silently ignored. A dry run writes nothing, so it never needs evidence.
    """
    if tool_name not in ATTESTED_TOOLS or params.get("dry_run") is True:
        return None
    text = _text(params.get("user_approval"))
    if tool_name == "approve":
        human = _text(params.get("confirmer"))
        if text is None:
            return None
    else:
        human = _text(params.get("approved_by"))
        if human is None and text is None:
            return None
    if human is None:
        raise _missing(
            "approved_by",
            "an attested action needs the human's name: pass --approved-by <name>",
        )
    if text is None:
        raise _missing(
            "user_approval",
            "an attested action needs the human's exact approval from chat: "
            'pass --user-approval "<their words>"',
        )
    return parse_attestation(
        {
            "schema": ATTESTATION_SCHEMA,
            "approved_by": human,
            "text": text,
            "recorded_by": attested_by or "agent",
            "channel": "chat",
            "verified": False,
        }
    )


def _missing(field: str, message: str) -> WriteRefused:
    return WriteRefused(
        "VALIDATION_FAILED",
        message,
        {"reason": "attestation_incomplete", "missing": field},
    )


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
