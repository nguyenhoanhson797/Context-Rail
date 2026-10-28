"""Bounded git calls for the executing side of verification and dispatch.

Every call is a list argv through :func:`context_rail.runner.run`: no shell, an
allow-listed environment, a hard timeout and capped output. Callers that only
need to read tracked paths and blob IDs use these helpers instead of parsing
``.git/index`` themselves, because they also see staged and untracked changes,
which a read must never trust as proof.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from context_rail.runner.run import run
from context_rail.store.write import WriteRefused

__all__ = [
    "git",
    "ls_files",
    "ls_files_staged",
    "rev_parse_head",
    "status_porcelain",
]

DEFAULT_TIMEOUT = 120.0


def git(
    args: Sequence[str], *, cwd: Path, confine_to: Path, timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """Run ``git <args>`` inside ``confine_to`` and return the bounded result."""
    if not args:
        raise WriteRefused("VALIDATION_FAILED", "git needs at least one argument")
    return run(
        ["git", *args],
        cwd=Path(cwd),
        confine_to=Path(confine_to),
        timeout=timeout,
    )


def ls_files(
    cwd: Path, confine_to: Path, paths: Sequence[str] | None = None
) -> dict[str, str]:
    """``{path: blob_oid}`` for the committed tree, from ``HEAD``.

    A path that is not committed is absent from the mapping. A missing or
    unreadable ``HEAD`` is a process error, never an empty proof.
    """
    args = ["ls-tree", "-r", "-z", "HEAD"]
    if paths:
        args += ["--", *paths]
    result = _checked(args, cwd=cwd, confine_to=confine_to)
    entries: dict[str, str] = {}
    for record in result["stdout"].split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) != 3 or not path:
            raise WriteRefused("PROCESS_ERROR", "git ls-tree printed an unknown record")
        entries[path] = fields[2]
    return entries


def ls_files_staged(
    cwd: Path, confine_to: Path, paths: Sequence[str] | None = None
) -> dict[str, str]:
    """``{path: blob_oid}`` for the index, so a staged change is visible.

    Skip-worktree entries are left out: git keeps no trustworthy worktree stat
    for them, so they can never prove a fresh check.
    """
    args = ["ls-files", "-s", "-t", "-z"]
    if paths:
        args += ["--", *paths]
    result = _checked(args, cwd=cwd, confine_to=confine_to)
    entries: dict[str, str] = {}
    for record in result["stdout"].split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) != 4 or not path:
            raise WriteRefused("PROCESS_ERROR", "git ls-files printed an unknown record")
        tag, _mode, oid, stage = fields
        if tag.upper() == "S" or stage != "0":
            continue
        entries[path] = oid
    return entries


def status_porcelain(cwd: Path, confine_to: Path) -> tuple[str, ...]:
    """The raw ``git status --porcelain`` entries, including untracked files."""
    result = _checked(
        ["status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=cwd,
        confine_to=confine_to,
    )
    return tuple(record for record in result["stdout"].split("\0") if record)


def rev_parse_head(cwd: Path, confine_to: Path) -> str:
    """The exact ``HEAD`` commit ``cwd`` points at."""
    result = _checked(["rev-parse", "--verify", "HEAD"], cwd=cwd, confine_to=confine_to)
    return result["stdout"].strip()


def _checked(args: Sequence[str], *, cwd: Path, confine_to: Path) -> dict:
    result = git(args, cwd=cwd, confine_to=confine_to)
    if result["timed_out"]:
        raise WriteRefused("PROCESS_ERROR", f"git {args[0]} timed out")
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR", f"git {args[0]} exited {result['exit']}: {detail}"
        )
    return result
