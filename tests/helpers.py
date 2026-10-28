"""Shared plain helpers for the Context Rail test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
