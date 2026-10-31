"""The one Git plumbing adapter for clone sync: argv builders, no shell."""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from context_rail.runner import git as git_runner
from context_rail.runner.run import run
from context_rail.store.write import WriteRefused

GIT_MAX_OUTPUT = 2_000_000
LOCAL_TIMEOUT = 120.0
NETWORK_TIMEOUT = 300.0
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_DRIVERS = ("doc", "record", "config")


def validate_name(value: object, what: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value) or value.startswith("-"):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} {value!r} is not a safe Git name", {"name": value}
        )
    return value


def identity_ok(cwd: Path, root: Path) -> bool:
    result = run(
        ["git", "var", "GIT_COMMITTER_IDENT"],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    return result["exit"] == 0 and not result["timed_out"]


def head_commit(cwd: Path, root: Path) -> str:
    return _git(["rev-parse", "--verify", "HEAD^{commit}"], cwd, root).strip()


def rev_parse(cwd: Path, root: Path, ref: str) -> str:
    return _git(
        ["rev-parse", "--verify", f"{_revision(ref, 'ref')}^{{commit}}"], cwd, root
    ).strip()


def upstream(cwd: Path, root: Path) -> str | None:
    result = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    if result["exit"] != 0:
        return None
    return result["stdout"].strip() or None


def operation_in_progress(cwd: Path, root: Path) -> bool:
    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
        result = run(
            ["git", "rev-parse", "-q", "--verify", marker],
            cwd=cwd,
            confine_to=root,
            timeout=LOCAL_TIMEOUT,
            max_output=GIT_MAX_OUTPUT,
        )
        if result["exit"] == 0:
            return True
    return False


def unmerged(cwd: Path, root: Path) -> tuple[str, ...]:
    text = _git(["ls-files", "-u"], cwd, root)
    return tuple(line.split("\t", 1)[-1] for line in text.splitlines() if line.strip())


def dirty_outside(cwd: Path, root: Path, store_rel: str) -> str | None:
    result = run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=no"],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    if result["omitted_bytes"]:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "git status output was truncated",
            {"process": "git"},
        )
    if result["exit"] != 0:
        raise WriteRefused(
            "INTEGRATION_FAILURE", "git status failed", {"process": "git"}
        )
    for token in result["stdout"].split("\0"):
        if len(token) < 4:
            continue
        path = token[3:]
        if not _under(path, store_rel):
            return path
    return None


def branch(cwd: Path, root: Path) -> str | None:
    result = run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    if result["exit"] != 0:
        return None
    ref = result["stdout"].strip()
    return ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else None


def stage_store(cwd: Path, root: Path, store_rel: str) -> None:
    _git(["--literal-pathspecs", "add", "--", store_rel], cwd, root)


def has_staged(cwd: Path, root: Path, pathspec: str) -> bool:
    result = run(
        ["git", "diff", "--cached", "--quiet", "--", pathspec],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    return result["exit"] == 1


def commit_local(cwd: Path, root: Path, message: str) -> str:
    _git(["commit", "-m", message, "--"], cwd, root)
    return head_commit(cwd, root)


def fetch(cwd: Path, root: Path, remote: str, branch: str) -> str:
    validate_name(remote, "remote")
    validate_name(branch, "branch")
    try:
        _git(
            ["fetch", "--no-tags", "--no-recurse-submodules", "--", remote, f"refs/heads/{branch}"],
            cwd,
            root,
            timeout=NETWORK_TIMEOUT,
        )
        return rev_parse(cwd, root, "FETCH_HEAD")
    except WriteRefused as exc:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "git fetch failed",
            {"process": "git", "phase": "fetch", "detail": exc.message},
        ) from None


def changed_paths(cwd: Path, root: Path, base: str, incoming: str) -> list[tuple[str, str]]:
    text = _git(
        [
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            _revision(base, "base"),
            _revision(incoming, "incoming"),
        ],
        cwd,
        root,
    )
    parts = [part for part in text.split("\0") if part]
    found: list[tuple[str, str]] = []
    for index in range(0, len(parts) - 1, 2):
        found.append((parts[index], parts[index + 1]))
    return found


def commits_between(cwd: Path, root: Path, base: str, incoming: str) -> list[list[str]]:
    text = _git(
        [
            "rev-list",
            "--parents",
            f"{_revision(base, 'base')}..{_revision(incoming, 'incoming')}",
        ],
        cwd,
        root,
    )
    return [line.split() for line in text.splitlines() if line.strip()]


def is_ancestor(cwd: Path, root: Path, ancestor: str, descendant: str) -> bool:
    result = run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            _revision(ancestor, "ancestor"),
            _revision(descendant, "descendant"),
        ],
        cwd=cwd,
        confine_to=root,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
    )
    return result["exit"] == 0


def worktree_add(cwd: Path, root: Path, scratch: Path, base: str) -> None:
    _git(
        ["worktree", "add", "--detach", "--", str(scratch), _revision(base, "base")],
        cwd,
        root,
    )


def merge(
    cwd: Path,
    scratch: Path,
    incoming: str,
    *,
    extra_env: dict[str, str] | None = None,
    attributes_file: Path | None = None,
) -> tuple[list[str], str]:
    command = ["git"]
    if attributes_file is not None:
        command += ["-c", f"core.attributesFile={attributes_file}"]
    command += ["merge", "--no-ff", "--no-commit", "--", _revision(incoming, "incoming")]
    result = run(
        command,
        cwd=scratch,
        confine_to=scratch,
        timeout=LOCAL_TIMEOUT,
        max_output=GIT_MAX_OUTPUT,
        extra_env=extra_env,
    )
    if result["exit"] == 0:
        return [], ""
    text = _git(
        ["diff", "--name-only", "--diff-filter=U", "-z"], scratch, scratch
    )
    paths = [path for path in text.split("\0") if path]
    if paths:
        return paths, str(result["stderr"])
    raise WriteRefused(
        "INTEGRATION_FAILURE",
        "git merge failed before producing a conflict",
        {"process": "git", "detail": str(result["stderr"])[:500]},
    )


def commit_scratch(cwd: Path, scratch: Path, message: str) -> str:
    _git(["commit", "-m", message, "--"], cwd, scratch)
    return head_commit(cwd, scratch)


def update_ref(cwd: Path, root: Path, branch_name: str, new: str, old: str, message: str) -> None:
    validate_name(branch_name, "branch")
    _git(
        [
            "update-ref",
            "-m",
            message,
            f"refs/heads/{branch_name}",
            _revision(new, "new"),
            _revision(old, "old"),
        ],
        cwd,
        root,
    )


def retain_ref(cwd: Path, root: Path, key: str, candidate: str) -> None:
    validate_name(key, "retained ref key")
    _git(
        ["update-ref", f"refs/rail/sync/{key}", _revision(candidate, "candidate")],
        cwd,
        root,
    )


def push(cwd: Path, root: Path, remote: str, branch_name: str, new: str) -> None:
    validate_name(remote, "remote")
    validate_name(branch_name, "branch")
    revision = _revision(new, "push commit")
    try:
        _git(
            ["push", "--porcelain", "--", remote, f"{revision}:refs/heads/{branch_name}"],
            cwd,
            root,
            timeout=NETWORK_TIMEOUT,
        )
    except WriteRefused as exc:
        raise WriteRefused(
            "CONFLICT",
            "the remote rejected the push",
            {"phase": "push", "detail": exc.message},
        ) from None


def worktree_remove(cwd: Path, root: Path, scratch: Path) -> None:
    try:
        _git(["worktree", "remove", "--", str(scratch)], cwd, root)
    except WriteRefused:
        return


def worktree_prune(cwd: Path, root: Path) -> None:
    _git(["worktree", "prune"], cwd, root)


def clone_driver_config() -> dict[str, str]:
    """The three clone-local driver values, quoted as posix paths."""
    exe = Path(sys.executable).as_posix()
    values: dict[str, str] = {}
    for kind in _DRIVERS:
        values[f"merge.rail-{kind}.driver"] = (
            f'"{exe}" -m context_rail sync driver {kind} %O %A %B %P'
        )
    return values


def set_local_driver(cwd: Path, root: Path) -> None:
    _git(["config", "--local", "core.autocrlf", "false"], cwd, root)
    for key, value in clone_driver_config().items():
        _git(["config", "--local", key, value], cwd, root)


def _git(
    args: list[str], cwd: Path, root: Path, *, timeout: float = LOCAL_TIMEOUT
) -> str:
    result = git_runner.git(
        args,
        cwd=cwd,
        confine_to=root,
        timeout=timeout,
        max_output=GIT_MAX_OUTPUT,
    )
    if result["omitted_bytes"]:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "git output was truncated",
            {"process": "git"},
        )
    if result["timed_out"]:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            f"git {args[0]} timed out",
            {"process": "git"},
        )
    if result["exit"] != 0:
        detail = str(result["stderr"] or result["stdout"]).strip()
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            f"git {args[0]} exited {result['exit']}",
            {"process": "git", "detail": detail[:500]},
        )
    return str(result["stdout"])


def _revision(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} must be a non-empty revision", {"revision": value}
        )
    if value.startswith("-") or any(character.isspace() for character in value):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} is not a plain revision", {"revision": value}
        )
    if "\x00" in value:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} contains a NUL byte", {"revision": value}
        )
    return value


def _under(path: str, store_rel: str) -> bool:
    path = path.replace("\\", "/")
    prefix = store_rel.rstrip("/") + "/"
    return path == store_rel or path.startswith(prefix)


def sha_key(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
