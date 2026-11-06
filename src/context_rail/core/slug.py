"""Slugs and digests, with nothing outside the standard library."""

from __future__ import annotations

import re
import unicodedata

__all__ = ["sha256_hex", "slugify"]


def slugify(text: str, max_len: int = 80) -> str:
    """Generate a URL-safe slug from text. Lowercase, hyphens, [a-z0-9-]."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    text = text.strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    # Loading OpenSSL costs about 10 ms, and a store at the repository root
    # never hashes, so the CLI fast path must not pay for it at import.
    import hashlib

    return hashlib.sha256(data).hexdigest()
