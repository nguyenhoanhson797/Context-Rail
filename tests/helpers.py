"""Shared plain helpers for the Context Rail test suite."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from context_rail.core.util import dump_frontmatter, load_yaml


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``cwd``; an error fails the calling test setup."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def touch_doc(store: Path, rel: str, **frontmatter: Any) -> None:
    """Replace frontmatter keys in an existing document and bump its mtime.

    The body is preserved and the mtime moves two seconds ahead, so a change is
    visible even on filesystems with coarse timestamps.
    """
    path = Path(store) / rel
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{rel} has no frontmatter")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    payload = dict(load_yaml("\n".join(lines[1:end])) or {})
    payload.update(frontmatter)
    body = "\n".join(lines[end + 1 :])
    path.write_text(
        "---\n" + dump_frontmatter(payload) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
