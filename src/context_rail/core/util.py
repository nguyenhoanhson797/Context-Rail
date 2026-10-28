"""Shared primitives: clocks, slugs, ULIDs, hashes, atomic bytes and YAML I/O."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string with second resolution."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_iso(value: str) -> str | None:
    """A timestamp as UTC seconds, so stored strings sort chronologically.

    Naive timestamps are read as UTC. Anything the parser rejects is ``None``:
    a cache row never invents a time.
    """
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str, max_len: int = 80) -> str:
    """Generate a URL-safe slug from text. Lowercase, hyphens, [a-z0-9-]."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    text = text.strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


# ── ULID ──────────────────────────────────────────────────────────────────

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ulid_lock = threading.Lock()
#: Last random part issued per millisecond, so two ids in one millisecond keep
#: increasing order instead of landing in a random order.
_ulid_last: dict[int, int] = {}


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(ms: int | None = None) -> str:
    """A 26-char Crockford ULID for ``ms`` (default now), monotonic per ms."""
    ms = int(time.time() * 1000) if ms is None else int(ms)
    with _ulid_lock:
        last = _ulid_last.get(ms)
        rand = secrets.randbits(80) if last is None else last + 1
        if rand >= 1 << 80:  # pragma: no cover - 2**80 events in one millisecond
            rand = 0
        _ulid_last[ms] = rand
        if len(_ulid_last) > 4096:
            for old in sorted(_ulid_last)[:2048]:
                del _ulid_last[old]
    return _encode(ms, 10) + _encode(rand, 16)


# ── Write primitives ──────────────────────────────────────────────────────


def write_bytes_atomic(path: Path, data: bytes, *, durable: bool = False) -> None:
    """Write bytes via a temp file in the same directory + ``os.replace``.

    On Windows a reader may hold the target open; the replace is retried
    briefly instead of failing the whole run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Strings PyYAML would coerce to a non-string type on the next parse (ISO
# timestamps/dates, YAML 1.1 bool/null tokens, bare numbers). They are quoted on
# write so values round-trip as strings: an unquoted ``2026-08-02T15:47:54Z``
# re-parses as a ``datetime`` and silently changes format on the next rewrite.
_COERCIBLE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?)?$"
    r"|^(yes|no|true|false|on|off|null|~|y|n)$"
    r"|^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$"
    r"|^[-+]?\.(inf|Inf|INF)$"
    r"|^\.(nan|NaN|NAN)$"
    r"|^0[xX][0-9a-fA-F]+$"
    r"|^0o[0-7]+$",
    re.IGNORECASE,
)


class _QuotedStr(str):
    """Marker: dump as a quoted YAML string even when the text looks coercible."""


def _repr_quoted(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="'")


yaml.SafeDumper.add_representer(_QuotedStr, _repr_quoted)


def _quote_coercible(value):
    """Wrap strings PyYAML would coerce, and normalize datetimes to Z form.

    Values that already parsed as ``datetime``/``date`` (from a previous
    unquoted write) are normalized to the canonical ``2026-08-02T15:47:54Z``
    string so repeated rewrites are idempotent.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # A naive timestamp keeps its wall-clock reading: relabeling it as
            # UTC would silently change the instant it names.
            return _QuotedStr(value.strftime("%Y-%m-%dT%H:%M:%S"))
        value = value.astimezone(timezone.utc)
        return _QuotedStr(value.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if isinstance(value, date):
        return _QuotedStr(value.isoformat())
    if isinstance(value, dict):
        return {k: _quote_coercible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_quote_coercible(v) for v in value]
    if isinstance(value, str) and value and _COERCIBLE_RE.match(value.strip()):
        return _QuotedStr(value)
    return value


def dump_frontmatter(frontmatter: dict) -> str:
    """Serialize frontmatter with lossless string round-tripping."""
    return yaml.safe_dump(
        _quote_coercible(frontmatter), sort_keys=False, allow_unicode=True
    )


def load_yaml(text: str) -> Any:
    """Parse YAML with the fast safe loader, never an unsafe one."""
    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
