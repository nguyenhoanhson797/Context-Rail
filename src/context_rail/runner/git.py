"""Bounded git calls for verification, candidates and the briefs built from them.

Every call is a list argv through :func:`context_rail.runner.run`: no shell, an
allow-listed environment, a hard timeout and capped output. Callers that only
need to read tracked paths and blob IDs use these helpers instead of parsing
``.git/index`` themselves, because they also see staged and untracked changes,
which a read must never trust as proof.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from context_rail.runner.run import run
from context_rail.store.write import WriteRefused

__all__ = [
    "CAPTURE_MAX_OUTPUT",
    "diff_names",
    "diff_text",
    "first_parent",
    "git",
    "ls_files",
    "ls_files_staged",
    "merge_base",
    "resolve_commit",
    "rev_parse_head",
    "status_porcelain",
    "worktree_paths",
]

DEFAULT_TIMEOUT = 120.0
#: A changed-file listing is a proof, not a log: read it generously so a large
#: candidate is never silently cut, and report it as truncated when it is.
CAPTURE_MAX_OUTPUT = 4 * 1024 * 1024
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def git(
    args: Sequence[str],
    *,
    cwd: Path,
    confine_to: Path,
    timeout: float = DEFAULT_TIMEOUT,
    max_output: int | None = None,
) -> dict:
    """Run ``git <args>`` inside ``confine_to`` and return the bounded result."""
    if not args:
        raise WriteRefused("VALIDATION_FAILED", "git needs at least one argument")
    extra = {} if max_output is None else {"max_output": max_output}
    return run(
        ["git", *args],
        cwd=Path(cwd),
        confine_to=Path(confine_to),
        timeout=timeout,
        **extra,
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
    # A truncated listing is never proof: read it generously and refuse a cut.
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
    # A truncated listing is never proof: read it generously and refuse a cut.
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
    # A truncated listing is never proof: read it generously and refuse a cut.
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


def resolve_commit(root: Path, rev: str) -> str | None:
    """The full sha of the commit ``rev`` names, or ``None`` when it names none.

    A revision that starts with ``-`` is never handed to git, where it would
    read as an option.
    """
    if (
        not isinstance(rev, str)
        or not rev
        or rev.startswith("-")
        or "\x00" in rev
        or any(character.isspace() for character in rev)
    ):
        return None
    result = git(
        ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        cwd=root,
        confine_to=root,
    )
    if result["timed_out"]:
        raise WriteRefused("PROCESS_ERROR", "git rev-parse timed out")
    sha = result["stdout"].strip()
    if result["exit"] != 0 or not _FULL_SHA.fullmatch(sha):
        return None
    return sha


def merge_base(root: Path, a: str, b: str) -> str | None:
    """The best common ancestor of ``a`` and ``b``; ``None`` when they share none."""
    result = git(
        ["merge-base", _revision(a, "a"), _revision(b, "b")],
        cwd=root,
        confine_to=root,
    )
    if result["timed_out"]:
        raise WriteRefused("PROCESS_ERROR", "git merge-base timed out")
    if result["exit"] == 1:
        return None
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR", f"git merge-base exited {result['exit']}: {detail}"
        )
    return result["stdout"].strip() or None


def first_parent(root: Path, commit: str) -> str | None:
    """The first parent of ``commit``; ``None`` for a root commit."""
    return resolve_commit(root, f"{_revision(commit, 'commit')}^1")


def diff_names(root: Path, base: str, commit: str) -> list[str]:
    """Paths ``commit`` changes relative to ``base``, sorted; a rename lists both its paths."""
    result = _checked(
        [
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            _revision(base, "base"),
            _revision(commit, "commit"),
            "--",
        ],
        cwd=root,
        confine_to=root,
    )
    return sorted({path for path in result["stdout"].split("\0") if path})


def diff_text(root: Path, base: str, commit: str, max_chars: int) -> tuple[str, bool]:
    """The patch from ``base`` to ``commit`` cut at ``max_chars``, and whether it was cut.

    External diff drivers and text conversion are off: both come from
    repository configuration and would run commands the caller never chose.
    """
    if max_chars < 0:
        raise WriteRefused("VALIDATION_FAILED", "max_chars must not be negative")
    # The runner keeps the head and the tail of an oversized output; reading
    # eight bytes per wanted character leaves a head of at least that many.
    result = _checked(
        [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            _revision(base, "base"),
            _revision(commit, "commit"),
            "--",
        ],
        cwd=root,
        confine_to=root,
        max_output=max_chars * 8 + 4096,
        allow_truncated=True,
    )
    text = result["stdout"]
    return text[:max_chars], bool(result["omitted_bytes"]) or len(text) > max_chars


def worktree_paths(root: Path) -> list[Path]:
    """The resolved path of every worktree of the repository at ``root``."""
    result = _checked(["worktree", "list", "--porcelain"], cwd=root, confine_to=root)
    return [
        Path(line[len("worktree ") :]).resolve()
        for line in result["stdout"].splitlines()
        if line.startswith("worktree ")
    ]


def _revision(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused("VALIDATION_FAILED", f"{what} must be a revision")
    if value.startswith("-") or any(character.isspace() for character in value):
        raise WriteRefused("VALIDATION_FAILED", f"{what} is not a plain revision: {value!r}")
    if "\x00" in value:
        raise WriteRefused("VALIDATION_FAILED", f"{what} contains a NUL byte")
    return value


def _checked(
    args: Sequence[str],
    *,
    cwd: Path,
    confine_to: Path,
    max_output: int = CAPTURE_MAX_OUTPUT,
    allow_truncated: bool = False,
) -> dict:
    result = git(args, cwd=cwd, confine_to=confine_to, max_output=max_output)
    if result["timed_out"]:
        raise WriteRefused("PROCESS_ERROR", f"git {args[0]} timed out")
    if result["omitted_bytes"] and not allow_truncated:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            f"git {args[0]} output was truncated",
            {"process": "git"},
        )
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR", f"git {args[0]} exited {result['exit']}: {detail}"
        )
    return result
