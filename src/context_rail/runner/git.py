"""Bounded git calls for the executing side of verification and dispatch.

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
from typing import Any

from context_rail.runner.run import run
from context_rail.store.location import owns_path
from context_rail.store.write import WriteRefused

__all__ = [
    "CAPTURE_LIMIT",
    "CAPTURE_MAX_OUTPUT",
    "capture_changes",
    "changed_line_ranges",
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
#: A changed-file listing is a proof, not a log: read it generously so a large
#: candidate is never silently cut, and report it as truncated when it is.
CAPTURE_MAX_OUTPUT = 4 * 1024 * 1024
#: The runner's own omission marker; a path carrying it was cut mid-entry.
_OMISSION_MARKER = "[omitted]"


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
    # A truncated listing is never proof: read it generously and refuse a cut.
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
    ``limit``; so is the diff listing itself, at :data:`CAPTURE_MAX_OUTPUT`
    bytes. Either cap makes the capture incomplete, and a listing the runner had
    to cut is reported as ``truncated`` rather than as a complete set of paths.
    """
    base = _revision(base_commit, "base_commit")
    candidate = _revision(candidate_commit, "candidate_commit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise WriteRefused("VALIDATION_FAILED", "a capture limit must be a positive integer")
    files_result = _checked(
        ["diff", "--name-only", "-z", base, candidate],
        cwd=worktree,
        confine_to=confine_to,
        max_output=CAPTURE_MAX_OUTPUT,
        allow_truncated=True,
    )
    commits_result = _checked(
        ["rev-list", "--reverse", f"{base}..{candidate}"],
        cwd=worktree,
        confine_to=confine_to,
        max_output=CAPTURE_MAX_OUTPUT,
        allow_truncated=True,
    )
    prefixes = tuple(
        text.replace("\\", "/").strip("/")
        for text in store_paths
        if isinstance(text, str) and text.strip("/")
    )
    files: list[str] = []
    store_changed: list[str] = []
    cut = False
    for name in sorted(files_result["stdout"].split("\0")):
        if not name.strip():
            continue
        if _OMISSION_MARKER in name:
            cut = True
            continue
        if owns_path(name, prefixes):
            store_changed.append(name)
        else:
            files.append(name)
    commits = [line.strip() for line in commits_result["stdout"].splitlines() if line.strip()]
    cut = cut or bool(files_result["omitted_bytes"]) or bool(commits_result["omitted_bytes"])
    omitted = (
        max(0, len(files) - limit)
        + max(0, len(commits) - limit)
        + max(0, len(store_changed) - limit)
    )
    if cut:
        reason: str | None = "truncated"
        # A cut listing cannot be counted, only bounded below: at least one
        # entry is missing, and the caller must never read zero as complete.
        omitted = max(omitted, 1)
    elif omitted:
        reason = "bounded"
    else:
        reason = None
    return {
        "files_changed": files[:limit],
        "commits_changed": commits[:limit],
        "store_changed": store_changed[:limit],
        "complete": reason is None,
        "reason": reason,
        "omitted": omitted,
    }


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def changed_line_ranges(
    *,
    worktree: Path,
    confine_to: Path,
    base_commit: str,
    candidate_commit: str = "HEAD",
) -> dict[str, Any]:
    """Old-side line ranges each file changed between two revisions.

    ``{"files": {path: [(start, end), ...]}, "complete": bool}``. Ranges are in
    the base revision's numbering, so they line up with a snapshot built from
    it. A pure insertion maps to the line it follows, a new file to no ranges,
    and a rename to a delete plus an add. A cut listing or a path git had to
    quote makes the result incomplete.
    """
    result = _checked(
        [
            "-c",
            "core.quotepath=false",
            "diff",
            "-U0",
            "--no-color",
            "--no-ext-diff",
            "--no-renames",
            _revision(base_commit, "base_commit"),
            _revision(candidate_commit, "candidate_commit"),
        ],
        cwd=worktree,
        confine_to=confine_to,
        max_output=CAPTURE_MAX_OUTPUT,
        allow_truncated=True,
    )
    complete = not result["omitted_bytes"]
    files: dict[str, list[tuple[int, int]]] = {}
    old = ""
    header = ""
    current: list[tuple[int, int]] | None = None
    for line in result["stdout"].splitlines():
        # A removed line "-- x" prints as "--- x", so file headers only count
        # right after a "diff --git" line.
        if line.startswith("diff --git "):
            header, current = "old", None
        elif header == "old" and line.startswith("--- "):
            old, header = line[4:].split("\t")[0], "new"
        elif header == "new" and line.startswith("+++ "):
            header = ""
            new = line[4:].split("\t")[0]
            name = _diff_path(new if new != "/dev/null" else old)
            if name is None:
                complete = False
                current = None
                continue
            current = files.setdefault(name, [])
            if old == "/dev/null":
                current = None
        elif current is not None and (hunk := _HUNK.match(line)):
            start = int(hunk.group(1))
            count = 1 if hunk.group(2) is None else int(hunk.group(2))
            current.append((max(start, 1), max(start, 1)) if count == 0 else (start, start + count - 1))
    return {"files": files, "complete": complete}


def _diff_path(text: str) -> str | None:
    if text.startswith('"') or text == "/dev/null":
        return None
    return text[2:] if text[:2] in ("a/", "b/") else None


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
