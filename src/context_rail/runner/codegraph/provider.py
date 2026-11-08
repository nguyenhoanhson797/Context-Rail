"""What every external code graph adapter has to provide.

The builtin extractor lives in this package; an external adapter is one module
with an ``extract`` function. This module holds the parts shared before an
adapter starts: the error shape, version gates, binary resolution and the
command used to launch a Python test double.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Protocol

from context_rail.model.codegraph import Snapshot

__all__ = [
    "BUILTIN_TIMEOUT",
    "EXTERNAL_TIMEOUT",
    "Adapter",
    "ExtractionError",
    "command_for",
    "node_version_ok",
    "parse_version",
    "resolve_binary",
    "version_between",
]

BUILTIN_TIMEOUT = 30.0
EXTERNAL_TIMEOUT = 120.0

_VERSION = re.compile(r"(?<![0-9])v?(\d+)\.(\d+)(?:\.(\d+))?(?![0-9])")


class ExtractionError(Exception):
    """An adapter could not produce a snapshot; the rail may fall back."""

    def __init__(self, kind: str, message: str, fix: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.fix = fix


class Adapter(Protocol):
    """One external extractor: committed tree and commit in, snapshot out."""

    def extract(
        self,
        worktree: Path,
        commit: str,
        *,
        cache_dir: Path,
        timeout_s: float,
    ) -> Snapshot: ...


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The first semantic version in ``text``; a missing patch counts as zero."""
    match = _VERSION.search(text or "")
    if match is None:
        return None
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return (int(major), int(minor), int(patch or 0))


def version_between(
    text: str, minimum: str, maximum: str | None = None
) -> bool:
    """Whether ``text`` is at least ``minimum`` and, when given, below ``maximum``."""
    found = parse_version(text)
    low = parse_version(minimum)
    high = None if maximum is None else parse_version(maximum)
    if found is None or low is None or (maximum is not None and high is None):
        return False
    if found < low:
        return False
    return high is None or found < high


def node_version_ok(text: str) -> bool:
    """The GitNexus runtime range: ``^22.18 || >=24.11``."""
    found = parse_version(text)
    if found is None:
        return False
    major, minor, _patch = found
    if major == 22:
        return minor >= 18
    if major == 24:
        return minor >= 11
    return major > 24


def resolve_binary(name: str, override: str | None) -> str | None:
    """The configured binary, else the one on ``PATH``."""
    if override:
        return override
    return shutil.which(name)


def command_for(binary: str) -> list[str]:
    """The argv prefix for a binary; a Python test double runs in this process."""
    if binary.lower().endswith(".py"):
        return [sys.executable, binary]
    return [binary]
