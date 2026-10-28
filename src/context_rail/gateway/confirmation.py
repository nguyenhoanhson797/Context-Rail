"""The one typed-confirmation phrase every surface builds and checks.

A confirmation is an exact, target-bound string the human types: not a boolean,
not a prefix and never an agent's prose. Keeping the builder in one place means
the CLI, the MCP server and the UI cannot drift apart, and a phrase for one
target never authorizes another.
"""

from __future__ import annotations

from context_rail.model.errors import ModelError

__all__ = ["ACTIONS", "confirmation_phrase", "validate_confirmation"]

ACTIONS = {
    "approve": "APPROVE {target}",
    "pass": "PASS {target}",
    "fail": "FAIL {target}",
    "pack_use": "USE PACK {target}",
    "adapters_force": "FORCE ADAPTERS {target}",
}


def confirmation_phrase(action: str, target: str) -> str:
    """The exact phrase ``action`` needs for ``target``."""
    template = ACTIONS.get(action) if isinstance(action, str) else None
    if template is None:
        raise ModelError(
            "VALIDATION_FAILED",
            "unknown confirmation action " + repr(action),
        )
    if not isinstance(target, str) or not target.strip():
        raise ModelError("VALIDATION_FAILED", "a confirmation needs a non-empty target")
    return template.format(target=target)


def validate_confirmation(action: str, target: str, supplied: object) -> str | None:
    """``None`` when ``supplied`` is exactly the phrase, else the reason it is not."""
    expected = confirmation_phrase(action, target)
    if not isinstance(supplied, str) or isinstance(supplied, bool):
        return f"the confirmation must be the text {expected!r}"
    if supplied != expected:
        return f"the confirmation must be exactly {expected!r}, not {supplied!r}"
    return None
