"""The one error the model layer may raise: a schema it refuses to guess at."""

from __future__ import annotations

from typing import Any

MODEL_CODES = ("SCHEMA_UNSUPPORTED", "VALIDATION_FAILED")


class ModelError(Exception):
    """A manifest or pack that cannot be used as written.

    ``code`` is one of ``MODEL_CODES`` so the gateway can map it straight onto
    the Result envelope.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        if code not in MODEL_CODES:
            raise ValueError(f"unknown model error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
