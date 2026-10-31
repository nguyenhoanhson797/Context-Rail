"""Store sync: the scratch-only merge driver and, later, clone integration."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterator, NoReturn

import yaml

from context_rail.core.util import (
    dump_frontmatter,
    load_yaml,
    new_ulid,
    now_iso,
    sha256_hex,
)
from context_rail.kernel.merge import Conflict, merge_frontmatter
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.runner import git_sync
from context_rail.runner.body_merge import merge_body
from context_rail.runner.dispatch import (
    DISPATCH_DIR,
    LOCK_DIR,
    acquire_tick_lock,
)
from context_rail.store.doc import etag, parse_doc
from context_rail.store.frontmatter_edit import join_frontmatter, split_frontmatter
from context_rail.store.gitattributes import managed_attributes
from context_rail.store.index import Index
from context_rail.store.location import local_dir
from context_rail.store.lock import StoreLockTimeout, file_lock
from context_rail.store.sync import (
    SyncIntent,
    candidate_differences,
    clear_intent,
    load_intent,
    save_intent,
    sync_key_path,
)
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, store_inventory

if TYPE_CHECKING:  # imported lazily by the gateway only when sync runs
    from context_rail.gateway.gateway import Context

KINDS = ("doc", "record", "config")


def sync_driver(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Merge one scratch file for Git; never read or write the live store."""
    kind = args["kind"]
    if kind not in KINDS:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "kind must be doc, record or config",
            {"kind": kind},
        )
    scratch, manifest = _scratch_context(ctx.store)
    path = _relative(args["path"])
    base = (
        scratch / "base.json"
        if kind == "record" and args["base"] == ""
        else _inside(scratch, args["base"])
    )
    ours = _inside(scratch, args["ours"])
    theirs = _inside(scratch, args["theirs"])
    if kind == "doc":
        return _merge_doc(scratch, path, base, ours, theirs)
    if kind == "record":
        return _merge_record(scratch, path, base, ours, theirs)
    return _merge_config(scratch, path, base, ours, theirs)


def _scratch_context(store: Path) -> tuple[Path, dict[str, Any]]:
    raw = os.environ.get("RAIL_SYNC_SCRATCH")
    if not raw:
        raise WriteRefused(
            "INVALID_STATE",
            "no sync scratch context",
            {"reason": "missing_scratch_manifest"},
        )
    manifest_path = Path(raw)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WriteRefused(
            "INVALID_STATE",
            "the sync scratch manifest is unreadable",
            {"path": str(manifest_path)},
        ) from None
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise WriteRefused("INVALID_STATE", "the sync scratch manifest is invalid")
    scratch = manifest_path.parent.resolve()
    if manifest.get("nonce") != scratch.name:
        raise WriteRefused("INVALID_STATE", "the scratch nonce does not match its folder")
    declared = manifest.get("store")
    if not isinstance(declared, str) or not declared:
        raise WriteRefused("INVALID_STATE", "the sync scratch manifest names no store")
    return scratch, manifest


def _inside(scratch: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise WriteRefused("INVALID_STATE", "a merge path is missing")
    path = Path(value).resolve()
    if not path.is_relative_to(scratch):
        raise WriteRefused(
            "INVALID_STATE", "a merge path is outside the scratch folder", {"path": str(path)}
        )
    return path


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WriteRefused("INVALID_STATE", "the merged path is missing")
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or ".." in parts or not parts:
        raise WriteRefused("VALIDATION_FAILED", "the merged path is not safe")
    return value


def _merge_doc(scratch: Path, path: str, base: Path, ours: Path, theirs: Path) -> dict[str, Any]:
    first = _parts(base)
    second = _parts(ours)
    third = _parts(theirs)
    if first is None or second is None or third is None:
        return _conflict(scratch, path, ["frontmatter"], "parse_error")
    metadata = merge_frontmatter(first[0], second[0], third[0])
    if metadata.conflicts or metadata.merged is None:
        return _conflict(
            scratch,
            path,
            [conflict.key for conflict in metadata.conflicts],
            "metadata_conflict",
            reasons=[conflict.reason for conflict in metadata.conflicts],
        )
    body = merge_body(first[1], second[1], third[1], scratch=scratch)
    if body.conflicted:
        return _conflict(scratch, path, [], body.reason or "body_conflict")
    if body.text is None:
        return _conflict(scratch, path, [], body.reason or "body_conflict")
    block = dump_frontmatter(metadata.merged).rstrip("\n")
    data = join_frontmatter(block, body.text).encode("utf-8")
    doc = parse_doc(path, data)
    if doc.problems:
        return _conflict(scratch, path, list(doc.problems), "parse_error")
    ours.write_bytes(data)
    return {"kind": "doc", "path": path, "status": "merged"}


def _parts(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    split = split_frontmatter(text)
    if split is None:
        return None
    block, rest = split
    try:
        fields = load_yaml(block)
    except (yaml.YAMLError, ValueError):
        return None
    if not isinstance(fields, dict):
        return None
    return fields, rest


def _merge_record(scratch: Path, path: str, base: Path, ours: Path, theirs: Path) -> dict[str, Any]:
    first = _read(base)
    second = _read(ours)
    third = _read(theirs)
    if first:
        if second != first or third != first:
            reason = "record_deleted" if not second else "record_modified"
            return _conflict(scratch, path, [], reason, ids=[PurePosixPath(path).stem])
        return {"kind": "record", "path": path, "status": "identical"}
    if second and third and second != third:
        return _conflict(
            scratch,
            path,
            [],
            "same_id_different_bytes",
            ids=[PurePosixPath(path).stem],
        )
    chosen = second or third
    ours.write_bytes(chosen)
    return {"kind": "record", "path": path, "status": "merged"}


def _merge_config(scratch: Path, path: str, base: Path, ours: Path, theirs: Path) -> dict[str, Any]:
    first = _read(base)
    second = _read(ours)
    third = _read(theirs)
    if second == third:
        chosen = second
    elif first == second:
        chosen = third
    elif first == third:
        chosen = second
    else:
        return _conflict(scratch, path, [], "config_divergent")
    ours.write_bytes(chosen)
    return {"kind": "config", "path": path, "status": "merged"}


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _conflict(
    scratch: Path,
    path: str,
    fields: list[str],
    reason: str,
    *,
    ids: list[str] | None = None,
    reasons: list[str] | None = None,
) -> NoReturn:
    folder = scratch / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    diagnostic = folder / f"{PurePosixPath(path).name}.{reason}.json"
    diagnostic.write_text(
        json.dumps(
            {"path": path, "fields": fields, "ids": ids or [], "reason": reason, "reasons": reasons or []},
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )
    raise WriteRefused(
        "CONFLICT",
        f"cannot merge {path!r}: {reason}",
        {
            "path": path,
            "fields": fields,
            "ids": ids or [],
            "reason": reason,
            "diagnostic": str(diagnostic),
        },
    )


def sync(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Install clone-local merge drivers, or integrate and publish store changes."""
    if args["install"]:
        return _install(ctx, args, actor)
    return _integrate(ctx, args, actor)


def _install(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    store = ctx.store
    root = _root(store)
    path = store / ".gitattributes"
    existing = path.read_bytes() if path.is_file() else None
    data = managed_attributes(existing)
    if data != existing:
        expect = etag(existing) if existing is not None else ABSENT
        commit(
            store,
            ctx.manifest,
            [Op(".gitattributes", data, expect)],
            None,
            actor=actor,
            key=args.get("key"),
            index=ctx.index,
        )
    git_sync.set_local_driver(root, root)
    return _result(
        phase="done",
        local_commit=None,
        integrated_commit=None,
        fetched_ref=None,
        pushed=False,
        paths=[".gitattributes"],
        conflicts=[],
        doctor={"error": 0, "warning": 0, "info": 0},
    )


def _integrate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    store = ctx.store
    root = _root(store)
    store_rel = _store_rel(store, root)
    with _sync_locks(store):
        existing = load_intent(store)
        if existing and existing.get("phase") in {"published", "committed"}:
            return _resume(ctx, existing, args, actor)
        if existing and existing.get("phase") in {"started", "merged"}:
            raise WriteRefused(
                "INVALID_STATE",
                "an earlier sync stopped before publication",
                {"phase": existing.get("phase")},
            )
        if ctx.approval["state"] != "approved":
            raise WriteRefused(
                "INVALID_STATE",
                "the charter is not approved",
                {"reason": ctx.approval["state"]},
            )
        if not git_sync.identity_ok(root, root):
            raise WriteRefused(
                "INTEGRATION_FAILURE",
                "git author identity is not configured",
                {"process": "git"},
            )
        if git_sync.operation_in_progress(root, root):
            raise WriteRefused("INVALID_STATE", "a merge or rebase is in progress")
        if git_sync.unmerged(root, root):
            raise WriteRefused("INVALID_STATE", "the index has unmerged paths")
        dirty = git_sync.dirty_outside(root, root, store_rel)
        if dirty is not None:
            raise WriteRefused(
                "INVALID_STATE", "changes outside the store are staged", {"path": dirty}
            )
        branch = args["branch"] or git_sync.branch(root, root)
        if branch is None:
            raise WriteRefused("INVALID_STATE", "HEAD is detached")
        remote = args["remote"]
        if remote is not None:
            git_sync.validate_name(remote, "remote")
        if args["branch"] is not None:
            git_sync.validate_name(args["branch"], "branch")
        if remote is None:
            found = git_sync.upstream(root, root)
            if found is None:
                raise WriteRefused(
                    "NOT_FOUND",
                    "the branch has no upstream and no remote was given",
                    {"branch": branch},
                )
            remote, _, branch = found.partition("/")
            if not branch:
                raise WriteRefused(
                    "NOT_FOUND", "the upstream names no branch", {"upstream": found}
                )
        git_sync.validate_name(remote, "remote")
        git_sync.validate_name(branch, "branch")
        key = args.get("key") or new_ulid()
        scratch = _scratch(store, key)
        intent = SyncIntent(
            key=key,
            phase="started",
            remote=remote,
            branch=branch,
            base="",
            incoming="",
            candidate_tree="",
            store_rel=store_rel,
            scratch=str(scratch),
        )
        save_intent(store, intent)

        try:
            git_sync.stage_store(root, root, store_rel)
            if git_sync.has_staged(root, root, store_rel):
                git_sync.commit_local(root, root, f"rail sync {key}")
            base = git_sync.head_commit(root, root)
            incoming = git_sync.fetch(root, root, remote, branch)
            changed = git_sync.changed_paths(root, root, base, incoming)
            outside = sorted(
                path for _status, path in changed if not _under(path, store_rel)
            )
            if outside:
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    "the incoming branch changes paths outside the store",
                    {"paths": outside},
                )
            if incoming == base:
                pushed = _push_if_allowed(ctx, root, remote, branch, base)
                clear_intent(store)
                return _result(
                    phase="done",
                    local_commit=base,
                    integrated_commit=None,
                    fetched_ref=incoming,
                    pushed=pushed,
                    paths=[],
                    conflicts=[],
                    doctor={"error": 0, "warning": 0, "info": 0},
                )
            git_sync.set_local_driver(root, root)
            git_sync.worktree_remove(root, root, scratch)
            shutil.rmtree(scratch, ignore_errors=True)
            git_sync.worktree_prune(root, root)
            git_sync.worktree_add(root, root, scratch, base)
            environment, attributes = _driver_env(scratch, store, store_rel)
            intent.base = base
            intent.incoming = incoming
            intent.phase = "merged"
            save_intent(store, intent)
            conflicts, merge_error = git_sync.merge(
                scratch,
                scratch,
                incoming,
                extra_env=environment,
                attributes_file=attributes,
            )
            if conflicts:
                _write_merge_diagnostics(scratch, conflicts)
                details = {
                    "phase": "merged",
                    "conflicts": conflicts,
                    "diagnostics": _read_diagnostics(scratch),
                    "detail": merge_error[:1000],
                }
                raise WriteRefused(
                    "CONFLICT",
                    "the incoming store changes conflict with this clone",
                    details,
                )
            candidate = _candidate(scratch, store_rel)
            _validate_candidate(candidate)
            live = dict(store_inventory(store))
            ops = candidate_differences(live, candidate)
            record = {
                "kind": "sync.integrated",
                "about": [],
                "data": {
                    "schema": 1,
                    "base": base,
                    "incoming": incoming,
                    "candidate_tree": _tree_hash(candidate),
                    "key": key,
                },
            }
            result = commit(
                store,
                ctx.manifest,
                ops,
                record,
                actor=actor,
                key=key,
                inventory=tuple(sorted(live.items())),
            )
            intent.record_id = result["record"]
            intent.candidate_tree = _tree_hash(candidate)
            intent.phase = "published"
            save_intent(store, intent)
        except WriteRefused:
            clear_intent(store)
            raise
        doctor = _doctor_counts(store)
        if doctor["error"]:
            raise WriteRefused(
                "INTEGRATION_FAILURE",
                "the merged store has doctor errors",
                {"phase": "committed", "doctor": doctor},
            )
        return _bookkeeping(
            ctx,
            intent,
            args,
            actor=actor,
            paths=sorted(op.path for op in ops),
            doctor=doctor,
        )


def _resume(
    ctx: Context, intent: dict[str, Any], args: dict[str, Any], actor: str
) -> dict[str, Any]:
    stored = SyncIntent(
        key=str(intent.get("key")),
        phase=str(intent.get("phase")),
        remote=str(intent.get("remote")),
        branch=str(intent.get("branch")),
        base=str(intent.get("base")),
        incoming=str(intent.get("incoming")),
        candidate_tree=str(intent.get("candidate_tree")),
        integrated=intent.get("integrated"),
        store_rel=str(intent.get("store_rel")),
        record_id=intent.get("record_id"),
        scratch=str(intent.get("scratch")),
    )
    return _bookkeeping(
        ctx,
        stored,
        args,
        actor=actor,
        paths=[],
        doctor=_doctor_counts(ctx.store),
    )


def _bookkeeping(
    ctx: Context,
    intent: SyncIntent,
    args: dict[str, Any],
    *,
    actor: str,
    paths: list[str],
    doctor: dict[str, int],
) -> dict[str, Any]:
    store = ctx.store
    root = _root(store)
    scratch = Path(intent.scratch)
    git_sync.set_local_driver(root, root)
    _copy_record(store, scratch, intent.store_rel, intent.record_id)
    git_sync.stage_store(scratch, scratch, intent.store_rel)
    integrated = git_sync.commit_scratch(scratch, scratch, f"rail sync {intent.key}")
    live = dict(store_inventory(store))
    scratch_candidate = _candidate(scratch, intent.store_rel)
    if scratch_candidate != {path: (store / path).read_bytes() for path in live}:
        outside = [
            path
            for path in scratch_candidate
            if not _under(path, intent.store_rel)
        ]
        if outside:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "a hook edited a path outside the store",
                {"paths": sorted(outside)},
            )
        ops = candidate_differences(live, scratch_candidate)
        commit(
            store,
            ctx.manifest,
            ops,
            None,
            actor=actor,
            index=ctx.index,
            inventory=tuple(sorted(live.items())),
        )
        doctor = _doctor_counts(store)
        if doctor["error"]:
            raise WriteRefused(
                "INTEGRATION_FAILURE",
                "a hook changed the store into doctor errors",
                {"phase": "committed", "doctor": doctor},
            )
    intent.integrated = integrated
    intent.phase = "committed"
    save_intent(store, intent)
    git_sync.retain_ref(root, root, intent.key, integrated)
    head = _head_now(root)
    if head != intent.base:
        raise WriteRefused(
            "CONFLICT",
            "HEAD moved while the sync was running",
            {"phase": "advance", "expected": intent.base, "actual": head},
        )
    try:
        git_sync.update_ref(
            root,
            root,
            intent.branch,
            integrated,
            intent.base,
            f"rail sync {intent.key}",
        )
    except WriteRefused as exc:
        raise WriteRefused(
            "CONFLICT",
            "the branch moved while the sync was running",
            {"phase": "advance", "detail": exc.message},
        ) from None
    intent.phase = "advanced"
    save_intent(store, intent)
    git_sync.stage_store(root, root, intent.store_rel)
    try:
        pushed = _push_if_allowed(
            ctx, root, intent.remote, intent.branch, integrated
        )
    except WriteRefused:
        clear_intent(store)
        raise
    if pushed:
        intent.phase = "pushed"
        save_intent(store, intent)
    git_sync.worktree_remove(root, root, scratch)
    clear_intent(store)
    return _result(
        phase="done",
        local_commit=intent.base,
        integrated_commit=integrated,
        fetched_ref=intent.incoming,
        pushed=pushed,
        paths=paths,
        conflicts=[],
        doctor=doctor,
    )


def _push_if_allowed(
    ctx: Context, root: Path, remote: str, branch: str, commit_id: str
) -> bool:
    """Push only an approved branch, and never rewrite the remote."""
    policy = ctx.policy
    if not bool(getattr(policy, "allow_push", False)):
        return False
    if branch != getattr(policy, "default_branch", None):
        return False
    git_sync.push(root, root, remote, branch, commit_id)
    return True


def _head_now(root: Path) -> str:
    return git_sync.head_commit(root, root)


def _root(store: Path) -> Path:
    from context_rail.runner.dispatch import project_root

    root = project_root(store)
    if root is None or not (Path(root) / ".git").exists():
        raise WriteRefused(
            "INVALID_STATE", "sync needs a git worktree", {"store": str(store)}
        )
    return Path(root)


def _store_rel(store: Path, root: Path) -> str:
    try:
        return store.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise WriteRefused("INVALID_STATE", "the store is outside its worktree") from None


def _scratch(store: Path, key: str) -> Path:
    digest = sha256_hex(key.encode("utf-8"))[:16]
    return local_dir(store) / "sync" / "scratch" / digest


def _driver_env(scratch: Path, store: Path, store_rel: str) -> tuple[dict[str, str], Path]:
    import context_rail

    manifest = scratch / ".rail-sync-manifest.json"
    scratch.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "store": str(store),
                "scratch": str(scratch),
                "nonce": scratch.name,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    prefix = "" if store_rel in ("", ".") else f"{store_rel}/"
    attributes = scratch / ".rail-attrs"
    attributes.write_text(
        "\n".join(
            [
                f"{prefix}**/*.md merge=rail-doc",
                f"{prefix}**/*.json merge=rail-record",
                f"{prefix}**/rail.yaml merge=rail-config",
                f"{prefix}**/packs/**/pack.yaml merge=rail-config",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    source = Path(context_rail.__file__).resolve().parents[1]
    existing_path = os.environ.get("PYTHONPATH", "")
    joined = str(source) + (os.pathsep + existing_path if existing_path else "")
    return {"RAIL_SYNC_SCRATCH": str(manifest), "PYTHONPATH": joined}, attributes


def _candidate(scratch: Path, store_rel: str) -> dict[str, bytes]:
    root = scratch if store_rel in ("", ".") else scratch / store_rel
    found: dict[str, bytes] = {}
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not _store_file(rel):
            continue
        found[rel] = path.read_bytes()
    return found


def _store_file(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if any(part.startswith(".") for part in parts[:-1]):
        return False
    if "templates" in parts[:-1] or rel.endswith(".tmp"):
        return False
    if rel == "rail.yaml":
        return True
    if rel.startswith("packs/") and rel.endswith("/pack.yaml"):
        return True
    return rel.endswith((".md", ".json"))


def _validate_candidate(candidate: dict[str, bytes]) -> None:
    if "rail.yaml" not in candidate:
        raise WriteRefused("VALIDATION_FAILED", "the candidate has no rail.yaml")
    try:
        data = yaml.safe_load(candidate["rail.yaml"].decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise WriteRefused("VALIDATION_FAILED", f"the candidate manifest is invalid: {exc}") from None
    if not isinstance(data, dict):
        raise WriteRefused("VALIDATION_FAILED", "the candidate manifest is not a mapping")


def _tree_hash(candidate: dict[str, bytes]) -> str:
    body = "\n".join(
        f"{path}:{hashlib.sha256(candidate[path]).hexdigest()}" for path in sorted(candidate)
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _under(path: str, store_rel: str) -> bool:
    path = path.replace("\\", "/")
    if store_rel in ("", "."):
        return True
    return path == store_rel or path.startswith(store_rel.rstrip("/") + "/")


def _doctor_counts(store: Path) -> dict[str, int]:
    from context_rail.doctor.checks import run_doctor

    manifest = load_manifest(store)
    pack = load_pack(manifest.pack, store)
    index = Index.open(store, pack)
    try:
        index.sync()
        report = run_doctor(store, manifest, pack, index)
    finally:
        index.close()
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in report["issues"]:
        severity = issue.get("severity", "info")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _copy_record(store: Path, scratch: Path, store_rel: str, record_id: Any) -> None:
    if not isinstance(record_id, str) or not record_id:
        return
    for path in (store / "records").rglob(f"{record_id}.json"):
        rel = path.relative_to(store)
        target = (scratch if store_rel in ("", ".") else scratch / store_rel) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        return


def _write_merge_diagnostics(scratch: Path, conflicts: list[str]) -> None:
    folder = scratch / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "merge.json").write_text(
        json.dumps({"conflicts": conflicts}, sort_keys=True), encoding="utf-8"
    )


def _read_diagnostics(scratch: Path) -> list[dict[str, Any]]:
    folder = scratch / "diagnostics"
    found: list[dict[str, Any]] = []
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


@contextlib.contextmanager
def _sync_locks(store: Path) -> Iterator[None]:
    with acquire_tick_lock(store):
        folder = local_dir(store) / DISPATCH_DIR / LOCK_DIR
        if folder.is_dir():
            for path in sorted(folder.glob("*.lock")):
                try:
                    with file_lock(path, timeout=0):
                        pass
                except StoreLockTimeout:
                    raise WriteRefused(
                        "CONFLICT", "a dispatch is still running", {"lock": str(path)}
                    ) from None
        yield


def _result(
    *,
    phase: str,
    local_commit: str | None,
    integrated_commit: str | None,
    fetched_ref: str | None,
    pushed: bool,
    paths: list[str],
    conflicts: list[dict[str, Any]],
    doctor: dict[str, int],
) -> dict[str, Any]:
    return {
        "phase": phase,
        "local_commit": local_commit,
        "integrated_commit": integrated_commit,
        "fetched_ref": fetched_ref,
        "pushed": pushed,
        "paths": paths,
        "conflicts": conflicts,
        "doctor": doctor,
        "next_action": "run sync again" if phase != "done" else "none",
    }
