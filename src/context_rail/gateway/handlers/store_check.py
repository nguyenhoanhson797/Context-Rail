"""Child store bounding, verification, and file-only freshness for ``store`` checks."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.locate import StoreLocationError, head_sha, worktree_git_dir
from context_rail.model.errors import ModelError
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.runner import git as git_runner
from context_rail.store.doc import etag
from context_rail.store.index import Index
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["resolve_child_store", "store_freshness", "store_payload"]

_MISSING_SENTINEL = "-"


def resolve_child_store(ctx: Context, declared: str) -> Path:
    """Resolve and bound a child store path against the master checkout's parent."""
    located = _master_worktree(ctx.store)
    if located is None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "store checks require the master store to live inside a git worktree",
            {"reason": "store_not_in_git", "store": declared},
        )
    master_root = located[1].resolve()
    bound = master_root.parent.resolve()
    normalized = declared.replace("\\", "/").rstrip("/")
    candidate = (master_root / normalized).resolve() if normalized else master_root
    if not candidate.is_relative_to(bound):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"child store {declared!r} resolves outside the master's parent folder",
            {"reason": "store_outside_bound", "store": declared},
        )
    if candidate == ctx.store.resolve():
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a store check cannot point at the master store itself",
            {"reason": "store_is_self", "store": declared},
        )
    if not (candidate / "rail.yaml").is_file():
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"child store {declared!r} has no rail.yaml",
            {"reason": "store_missing", "store": declared},
        )
    return candidate


def store_payload(ctx: Context, row: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one ``store`` check against the child store and return a verdict payload."""
    from context_rail.gateway.handlers.checks import _payload, _status_paths

    spec = row["spec"]
    declared = str(spec.get("store") or "")
    item = str(spec.get("item") or "")
    expected_category = str(spec.get("category") or "")

    child = resolve_child_store(ctx, declared)
    master_located = _master_worktree(ctx.store)
    master_commit = (
        git_runner.rev_parse_head(master_located[1], master_located[1])
        if master_located is not None
        else None
    )

    def fail_verdict(
        reason: str,
        *,
        doc: str = _MISSING_SENTINEL,
        commit: str = _MISSING_SENTINEL,
        doc_etag: str = _MISSING_SENTINEL,
        category: str = expected_category,
    ) -> dict[str, Any]:
        return _payload(
            row,
            passed=False,
            reason=reason,
            candidate_commit=master_commit,
            store={
                "path": declared,
                "item": item,
                "doc": doc,
                "commit": commit,
                "etag": doc_etag,
                "category": category,
            },
        )

    try:
        manifest = load_manifest(child)
        pack = load_pack(manifest.pack, child)
    except ModelError as error:
        return fail_verdict(error.message)

    child_index = Index.open(child, pack)
    try:
        child_index.sync()
        matches = child_index.conn.execute(
            "SELECT path, status, broken FROM docs WHERE slug = ? AND primitive = 'item' ORDER BY path",
            (item,),
        ).fetchall()
    finally:
        child_index.close()

    if not matches:
        return fail_verdict(f"child item {item!r} was not found")
    if len(matches) > 1:
        return fail_verdict(f"child item {item!r} is ambiguous ({len(matches)} documents)")

    rel_doc, status, broken = matches[0]
    if broken:
        return fail_verdict(f"child item {item!r} at {rel_doc!r} could not be parsed", doc=rel_doc)

    doc_path = child / rel_doc
    if not doc_path.is_file():
        return fail_verdict(f"child item {item!r} at {rel_doc!r} is missing", doc=rel_doc)
    doc_etag = etag(doc_path.read_bytes())

    child_located = _master_worktree(child)
    if child_located is None:
        return fail_verdict(
            "child store is not inside a git worktree",
            doc=rel_doc,
            doc_etag=doc_etag,
        )
    child_root = child_located[1].resolve()
    commit = head_sha(child) or git_runner.rev_parse_head(child_root, child_root)
    try:
        repo_rel = doc_path.resolve().relative_to(child_root).as_posix()
    except ValueError:
        return fail_verdict(
            f"child item {item!r} at {rel_doc!r} lies outside the child git root",
            doc=rel_doc,
            commit=commit,
            doc_etag=doc_etag,
        )

    staged = git_runner.ls_files_staged(child_root, child_root)
    untracked, dirty = _status_paths(child_root)
    if repo_rel in untracked or repo_rel not in staged:
        return fail_verdict(
            f"child item {item!r} at {repo_rel!r} is untracked",
            doc=rel_doc,
            commit=commit,
            doc_etag=doc_etag,
        )
    if repo_rel in dirty:
        return fail_verdict(
            f"child item {item!r} at {repo_rel!r} has uncommitted changes",
            doc=rel_doc,
            commit=commit,
            doc_etag=doc_etag,
        )

    observed_category = pack.statuses.get(status) if isinstance(status, str) else None
    if observed_category is None:
        return fail_verdict(
            f"child item {item!r} has unknown status {status!r}",
            doc=rel_doc,
            commit=commit,
            doc_etag=doc_etag,
        )

    passed = observed_category == expected_category
    reason = (
        None
        if passed
        else f"child item {item!r} is in category {observed_category!r}, expected {expected_category!r}"
    )
    return _payload(
        row,
        passed=passed,
        reason=reason,
        candidate_commit=master_commit,
        store={
            "path": declared,
            "item": item,
            "doc": rel_doc,
            "commit": commit,
            "etag": doc_etag,
            "category": observed_category,
        },
    )


def store_freshness(ctx: Context, row: Mapping[str, Any]) -> str:
    """Compute file-only freshness of a ``store`` check without spawning git."""
    if row.get("verdict") is None:
        return "missing"
    if row.get("passed") is not True or row.get("check_etag") != row.get("definition_etag"):
        return "stale"
    binding = row.get("store_binding")
    if not isinstance(binding, Mapping):
        return "unknown"
    declared = binding.get("path")
    rel_doc = binding.get("doc")
    recorded_commit = binding.get("commit")
    recorded_etag = binding.get("etag")
    if not all(isinstance(v, str) and v for v in (declared, rel_doc, recorded_commit, recorded_etag)):
        return "unknown"

    located = _master_worktree(ctx.store)
    if located is None:
        return "unknown"
    master_root = located[1].resolve()
    bound = master_root.parent.resolve()
    normalized = str(declared).replace("\\", "/").rstrip("/")
    if not normalized:
        return "unknown"
    try:
        child = (master_root / normalized).resolve()
    except OSError:
        return "unknown"
    if not child.is_relative_to(bound) or child == ctx.store.resolve():
        return "unknown"
    if not (child / "rail.yaml").is_file():
        return "unknown"

    current_head = head_sha(child)
    if current_head is None:
        return "unknown"
    doc_path = child / str(rel_doc)
    try:
        if not doc_path.is_file():
            return "unknown"
        current_etag = etag(doc_path.read_bytes())
    except OSError:
        return "unknown"

    if current_head != recorded_commit or current_etag != recorded_etag:
        return "stale"
    return "fresh"


def _master_worktree(store: Path) -> tuple[Path, Path] | None:
    try:
        return worktree_git_dir(store)
    except StoreLocationError:
        return None
