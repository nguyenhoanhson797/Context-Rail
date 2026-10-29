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
    "CAPTURE_LIMIT",
    "capture_changes",
    "current_branch",
    "fast_forward",
    "git",
    "is_ancestor",
    "ls_files",
    "ls_files_staged",
    "push",
    "rebase",
    "rebase_in_progress",
    "rev_parse_head",
    "status_porcelain",
    "unmerged_paths",
]

DEFAULT_TIMEOUT = 120.0
CAPTURE_LIMIT = 200


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


def current_branch(cwd: Path, confine_to: Path) -> str:
    """The checked-out branch of ``cwd``; ``HEAD`` while it is detached."""
    result = _checked(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, confine_to=confine_to
    )
    return result["stdout"].strip()


def is_ancestor(
    cwd: Path, confine_to: Path, ancestor: str, descendant: str = "HEAD"
) -> bool:
    """Whether ``ancestor`` is already contained in ``descendant``."""
    result = git(
        ["merge-base", "--is-ancestor", _revision(ancestor, "ancestor"), _revision(descendant, "descendant")],
        cwd=cwd,
        confine_to=confine_to,
    )
    if result["timed_out"]:
        raise WriteRefused("PROCESS_ERROR", "git merge-base timed out")
    if result["exit"] == 0:
        return True
    if result["exit"] == 1:
        return False
    detail = (result["stderr"] or result["stdout"]).strip()
    raise WriteRefused(
        "PROCESS_ERROR", f"git merge-base exited {result['exit']}: {detail}"
    )


def rebase(cwd: Path, confine_to: Path, onto: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """``git rebase onto`` in ``cwd``; a nonzero exit is for the caller to read."""
    return git(
        ["rebase", _revision(onto, "onto")],
        cwd=cwd,
        confine_to=confine_to,
        timeout=timeout,
    )


def rebase_in_progress(cwd: Path, confine_to: Path) -> bool:
    """Whether a rebase has stopped in this worktree, conflicted or not."""
    for name in ("rebase-merge", "rebase-apply"):
        result = _checked(
            ["rev-parse", "--git-path", name], cwd=cwd, confine_to=confine_to
        )
        reported = result["stdout"].strip()
        path = Path(reported)
        if not path.is_absolute():
            path = Path(cwd) / path
        if path.exists():
            return True
    return False


def unmerged_paths(cwd: Path, confine_to: Path) -> tuple[str, ...]:
    """The paths git still holds as unresolved conflicts, sorted."""
    result = _checked(
        ["diff", "--name-only", "--diff-filter=U", "-z"],
        cwd=cwd,
        confine_to=confine_to,
    )
    return tuple(sorted(path for path in result["stdout"].split("\0") if path))


def fast_forward(
    cwd: Path, confine_to: Path, candidate: str, timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """``git merge --ff-only candidate``; never a two-parent merge."""
    return git(
        ["merge", "--ff-only", _revision(candidate, "candidate")],
        cwd=cwd,
        confine_to=confine_to,
        timeout=timeout,
    )


def push(
    cwd: Path,
    confine_to: Path,
    remote: str,
    branch: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """``git push remote refs/heads/branch``; never a tag, force or delete."""
    for value, what in ((remote, "remote"), (branch, "branch")):
        if not isinstance(value, str) or not value.strip():
            raise WriteRefused("VALIDATION_FAILED", f"a push needs a {what}")
        if value.startswith("-") or any(character.isspace() for character in value):
            raise WriteRefused("VALIDATION_FAILED", f"the {what} {value!r} is not plain")
    ref = f"refs/heads/{branch}"
    return git(
        ["push", remote, f"{ref}:{ref}"], cwd=cwd, confine_to=confine_to, timeout=timeout
    )


def capture_changes(
    *,
    worktree: Path,
    confine_to: Path,
    base_commit: str,
    candidate_commit: str = "HEAD",
    store_paths: Sequence[str] = ("context-rail",),
    limit: int = CAPTURE_LIMIT,
) -> dict:
    """Changed files and commits between two revisions, bounded.

    Store-owned paths are reported separately: they are observations the parent
    must refuse rather than authorship proof. Both lists are capped at
    ``limit``; a capped capture is incomplete and says how much it dropped.
    """
    base = _revision(base_commit, "base_commit")
    candidate = _revision(candidate_commit, "candidate_commit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise WriteRefused("VALIDATION_FAILED", "a capture limit must be a positive integer")
    files_result = _checked(
        ["diff", "--name-only", "-z", base, candidate],
        cwd=worktree,
        confine_to=confine_to,
    )
    commits_result = _checked(
        ["rev-list", "--reverse", f"{base}..{candidate}"],
        cwd=worktree,
        confine_to=confine_to,
    )
    prefixes = tuple(
        text.replace("\\", "/").strip("/")
        for text in store_paths
        if isinstance(text, str) and text.strip("/")
    )
    files: list[str] = []
    store_changed: list[str] = []
    for path in sorted(name for name in files_result["stdout"].split("\0") if name):
        if _inside(path, prefixes):
            store_changed.append(path)
        else:
            files.append(path)
    commits = [line.strip() for line in commits_result["stdout"].splitlines() if line.strip()]
    omitted = max(0, len(files) - limit) + max(0, len(commits) - limit)
    return {
        "files_changed": files[:limit],
        "commits_changed": commits[:limit],
        "store_changed": store_changed[:limit],
        "complete": omitted == 0,
        "reason": None if omitted == 0 else "bounded",
        "omitted": omitted,
    }


def _revision(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused("VALIDATION_FAILED", f"{what} must be a revision")
    if value.startswith("-") or any(character.isspace() for character in value):
        raise WriteRefused("VALIDATION_FAILED", f"{what} is not a plain revision: {value!r}")
    if "\x00" in value:
        raise WriteRefused("VALIDATION_FAILED", f"{what} contains a NUL byte")
    return value


def _inside(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


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
