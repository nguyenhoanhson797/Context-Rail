"""The git working state a dispatch starts from: which paths the store owns, the
checkpoint commit that carries them into a new worktree, and the worktree itself."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.runner import dispatch as local_dispatch, git as git_runner
from context_rail.store.location import owns_path
from context_rail.store.write import WriteRefused

from .common import ADAPTER_PATHS, CHECKOUT_MESSAGE

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _store_paths(ctx: Context, root: Path) -> tuple[str, ...]:
    """The checkout-relative store folder plus the adapters, as path prefixes.

    A store rooted at the checkout itself has no folder of its own, so ``.``
    stands for the whole tree; :func:`_inside` reads it that way.
    """
    try:
        rel = Path(ctx.store).resolve().relative_to(root).as_posix()
    except ValueError:
        rel = Path(ctx.store).name
    return tuple([rel, *ADAPTER_PATHS])


def _checkpoint(
    ctx: Context,
    root: Path,
    store_paths: tuple[str, ...],
    actor: str,
    *,
    message: str = CHECKOUT_MESSAGE,
) -> str:
    """Commit only store-owned work so the default branch stays usable.

    Anything dirty outside the store and its adapters is refused: a dispatch or
    a merge never stashes, discards or commits a human's unrelated work.
    """
    entries = _status_entries(root)
    if not entries:
        return git_runner.rev_parse_head(root, root)
    foreign = [path for path in entries if not owns_path(path, store_paths)]
    if foreign:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout has changes outside the store",
            {"paths": foreign[:20]},
        )
    present = [path for path in store_paths if (root / path).exists()]
    if not present:
        raise WriteRefused(
            "INVALID_STATE",
            "the project checkout has no store folder to checkpoint",
            {"paths": entries[:20]},
        )
    _stage_store(root, present)
    _git(["commit", "-m", message], root)
    return git_runner.rev_parse_head(root, root)


def _stage_store(root: Path, present: list[str]) -> None:
    """Stage the store paths, forcing them past any global excludesfile.

    ``git add`` exits nonzero when an excludesfile drops one of the paths; a
    checkpoint that ignored that exit would commit a partial store and report
    success. The forced retry keeps adapter paths, then a second failure is a
    process error, never a silent omission.
    """
    staged = _git(["add", "-A", "--", *present], root)
    if staged["exit"] == 0 and not staged["timed_out"]:
        return
    forced = _git(["add", "-A", "--force", "--", *present], root)
    if forced["exit"] == 0 and not forced["timed_out"]:
        return
    detail = (forced["stderr"] or forced["stdout"] or staged["stderr"] or "").strip()
    raise WriteRefused(
        "PROCESS_ERROR",
        f"the store paths could not be staged: {detail or 'git add failed'}",
        {"paths": present[:20]},
    )


def _status_entries(root: Path) -> list[str]:
    paths: list[str] = []
    for entry in git_runner.status_porcelain(root, root):
        paths.append(entry[3:] if len(entry) > 3 and entry[2] == " " else entry)
    return sorted(path.replace("\\", "/") for path in paths if path)


def _create_worktree(root: Path, paths: local_dispatch.DispatchPaths, revision: str) -> None:
    if not isinstance(revision, str) or not revision.strip():
        raise WriteRefused("VALIDATION_FAILED", "a worktree needs a revision to start from")
    existing = _git(["branch", "--list", paths.branch], root)
    if existing["stdout"].strip():
        raise WriteRefused(
            "CONFLICT",
            f"the branch {paths.branch!r} already exists",
            {"branch": paths.branch},
        )
    if paths.worktree.exists():
        if any(paths.worktree.iterdir()):
            raise WriteRefused(
                "CONFLICT",
                "the worktree directory already exists",
                {"worktree": str(paths.worktree)},
            )
        paths.worktree.rmdir()
    paths.root.mkdir(parents=True, exist_ok=True)
    added = _git(
        ["worktree", "add", "-b", paths.branch, str(paths.worktree), revision],
        root,
        timeout=120.0,
    )
    if added["exit"] != 0:
        detail = (added["stderr"] or added["stdout"]).strip()
        raise WriteRefused(
            "PROCESS_ERROR",
            f"git worktree add failed: {detail or 'no output'}",
            {"branch": paths.branch, "worktree": str(paths.worktree)},
        )
    head = git_runner.rev_parse_head(paths.worktree, paths.worktree)
    expected = _resolve_commit(root, revision)
    if head != expected:
        raise WriteRefused(
            "PROCESS_ERROR",
            "the new worktree is not at the requested revision",
            {"expected": expected, "actual": head},
        )


def _resolve_commit(root: Path, revision: str) -> str:
    result = _git(["rev-parse", "--verify", f"{revision}^{{commit}}"], root)
    if result["exit"] != 0:
        raise WriteRefused(
            "INVALID_STATE",
            f"the revision {revision!r} is not a commit in this project",
            {"revision": revision},
        )
    return result["stdout"].strip()


def _git(args: list[str], cwd: Path, timeout: float = 120.0) -> dict[str, Any]:
    return git_runner.git(args, cwd=cwd, confine_to=cwd, timeout=timeout)


def _current_branch(root: Path) -> str:
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return result["stdout"].strip()
