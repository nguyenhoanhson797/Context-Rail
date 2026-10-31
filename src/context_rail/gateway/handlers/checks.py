"""Check declaration and bounded verification.

A Check is a store document whose meaning is its validated spec. Verifying it
appends one immutable verdict Record bound to the declared inputs: a command
verdict carries the blob IDs git recorded for those inputs and the runner
digest of its bounded output. Missing, untracked or dirty inputs never produce
proof, and nothing is ever staged to make a check pass.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import sha256_hex
from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.handlers.common import (
    compose_doc,
    preset_for,
    template_body,
)
from context_rail.kernel.rights import review_separation
from context_rail.model.cto import Check, parse_check, parse_verdict
from context_rail.model.manifest import render_path
from context_rail.runner import git as git_runner
from context_rail.runner.run import run as run_command
from context_rail.store import query
from context_rail.store.doc import etag, parse_doc
from context_rail.store.git_index import (
    GlobError,
    IndexEntry,
    IndexSnapshot,
    freshness,
    invalid_glob,
    match_inputs,
    read_index,
    selected_stats,
)
from context_rail.store.location import local_dir, worktree_git_dir
from context_rail.store.lock import store_lock
from context_rail.store.write import (
    ABSENT,
    Op,
    WriteRefused,
    commit,
    replay,
)

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = ["check", "check_decision", "freshness_of", "verify", "verify_decision"]

MAX_SPEC_BYTES = 256 * 1024
MAX_TIMEOUT_S = 3600
DEFAULT_TIMEOUT_S = 120
EMPTY_DIGEST = "sha256:" + sha256_hex(b"")
HUMAN_VERDICTS = ("pass", "fail")
SHELL_MODES = ("cmd", "exists")
#: The modes whose verification may carry a supplied ``--evidence`` list.
EVIDENCE_MODES = ("evidence", "human")
#: Modes an Item-wide verification leaves out: a human verdict is the human's,
#: and a review proves one exact candidate the review command produced.
ITEM_WIDE_SKIP = ("human", "review")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_RECORD_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_EXECUTABLE_MODES = ("cmd", "exists", "evidence", "review")


class _DuplicateKey(Exception):
    """A JSON object repeated a key; the spec is refused instead of guessed."""


def check(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create or edit one Check document and its declaration Record."""
    slug = _check_slug(args["slug"])
    expect = args["expect"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _declared_response(store, saved)
        spec = parse_check(_read_spec(args["spec_file"]))
        _validate_definition(spec)
        preset = preset_for(ctx.pack, "check")
        rel = render_path(
            ctx.manifest, primitive=preset.primitive, preset=preset.name, slug=slug
        )
        path = store / rel
        exists = path.is_file()
        used = [
            other
            for (other,) in index.conn.execute(
                "SELECT path FROM docs WHERE slug = ? ORDER BY path", (slug,)
            )
            if other != rel
        ]
        if used:
            raise WriteRefused(
                "CONFLICT",
                f"the slug {slug!r} is already used by another document",
                {"paths": used},
            )
        target = _trimmed(args.get("target"))
        if expect == ABSENT:
            if exists:
                raise WriteRefused(
                    "CONFLICT", f"{rel!r} already exists", {"path": rel}
                )
            current = None
        else:
            if not exists:
                raise LookupError("not_found")
            data = path.read_bytes()
            actual = etag(data)
            if actual != expect:
                raise WriteRefused(
                    "CONFLICT",
                    f"{rel!r} is at etag {actual}, not {expect}",
                    {"path": rel, "expected": expect, "actual": actual},
                )
            current = parse_doc(rel, data)
            if current.primitive != "check":
                raise WriteRefused(
                    "VALIDATION_FAILED", f"{rel!r} is not a check", {"path": rel}
                )
            if target is None:
                inherited = current.relations.get("verifies", ())
                target = inherited[0] if inherited else None
        declared = spec.verifies[0] if spec.verifies else None
        if target is not None and declared is not None and target != declared:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the target argument and the spec's verifies disagree",
                {"target": target, "verifies": declared},
            )
        item = target or declared
        if item is not None:
            _require_item(index, item)
        elif current is None:
            raise WriteRefused(
                "VALIDATION_FAILED", "a new check needs the Item it verifies",
                {"slug": slug},
            )
        if _is_acceptance(index, slug, current, spec) and actor != "human":
            raise WriteRefused(
                "PERMISSION_DENIED",
                "acceptance checks are written by the human only",
                {"slug": slug},
            )
        front = _frontmatter(preset.name, spec, item)
        body = template_body(store, ctx.pack, preset, slug)
        record_about = [slug, item] if item is not None else [slug]
        result = commit(
            store,
            ctx.manifest,
            [Op(rel, compose_doc(front, body), expect)],
            {
                "kind": "check.declared",
                "about": record_about,
                "data": {
                    "schema": 1,
                    "check": slug,
                    "item": item,
                    "path": rel,
                    "mode": spec.mode,
                    "acceptance": spec.acceptance,
                },
            },
            actor=actor,
            key=key,
            index=index,
        )
    return _declared_response(store, result)


def verify(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Verify one Check, or every executable Check one Item declares."""
    target = _trimmed(args.get("target"))
    if target is None:
        raise WriteRefused("VALIDATION_FAILED", "verify needs a check or Item slug")
    index = ctx.index
    row = query.check_state(index, target)
    if row is not None:
        rows, single = [row], True
    else:
        found = index.conn.execute(
            "SELECT slug FROM docs WHERE primitive = 'item' AND slug = ? "
            "AND broken = 0 LIMIT 1",
            (target,),
        ).fetchone()
        if found is None:
            raise LookupError("not_found")
        rows = query.check_states(index, target)
        if not rows:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{target!r} declares no checks", {"item": target}
            )
        single = False
    supplied = {
        "verdict": _trimmed(args.get("verdict")),
        "confirm": args.get("confirm"),
        "evidence": list(args.get("evidence") or ()),
    }
    if supplied["verdict"] is not None or supplied["confirm"] is not None:
        if not single:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "verdict and confirm apply to one Check",
                {"target": target},
            )
        if rows[0]["mode"] != "human":
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"a verdict is not valid for mode {rows[0]['mode']!r}",
                {"check": rows[0]["slug"], "mode": rows[0]["mode"]},
            )
    elif supplied["evidence"]:
        if not single:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "evidence applies to one Check",
                {"target": target},
            )
        if rows[0]["mode"] not in EVIDENCE_MODES:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"evidence is not valid for mode {rows[0]['mode']!r}",
                {"check": rows[0]["slug"], "mode": rows[0]["mode"]},
            )
    key = args["key"]
    worktree = _resolve_worktree(ctx, args.get("worktree"))
    results: list[dict[str, Any]] = []
    for selected in rows:
        if not single and selected["mode"] in ITEM_WIDE_SKIP:
            continue
        target_worktree = worktree if selected["mode"] in SHELL_MODES else None
        results.append(
            _verify_one(ctx, selected, supplied, actor, target_worktree, key)
        )
    if not results:
        return {
            "verdicts": [],
            "passed": False,
            "reason": "no_executable_checks",
        }
    return {
        "verdicts": results,
        "passed": bool(results) and all(entry["passed"] for entry in results),
        "reason": next((entry["reason"] for entry in results if entry["reason"]), None),
    }


def check_decision(ctx: Context, params: Mapping[str, Any]) -> str:
    """The class a declaration needs: acceptance edits are the human's."""
    slug = params.get("slug")
    existing = None
    if isinstance(slug, str) and slug.strip():
        try:
            existing = query.check_state(ctx.index, slug.strip())
        except query.QueryError:
            existing = None
    spec = _spec_quiet(params.get("spec_file"))
    acceptance = bool(existing and existing["acceptance"]) or (
        isinstance(spec, dict) and spec.get("acceptance") is True
    )
    return "human-only" if acceptance else "cto-alone"


def verify_decision(ctx: Context, params: Mapping[str, Any]) -> str:
    """The class verification needs: a human Check can only be verified by a human."""
    target = params.get("target")
    if not isinstance(target, str) or not target.strip():
        return "cto-alone"
    try:
        row = query.check_state(ctx.index, target.strip())
    except query.QueryError:
        return "cto-alone"
    if row is not None and row["mode"] == "human":
        return "human-only"
    return "cto-alone"


def freshness_of(
    ctx: Context, row: Mapping[str, Any] | None, *, worktree: Path | None = None
) -> str:
    """How much a stored verdict still says about a checkout, from files only.

    ``worktree`` names the checkout the proof should describe, which is the
    dispatch worktree while its candidate has not reached the default branch.
    """
    if row is None:
        return "missing"
    verdict: dict[str, Any] | None = None
    if row.get("verdict") is not None:
        verdict = {
            "passed": row.get("passed"),
            "check_etag": row.get("check_etag"),
            "input_blobs": row.get("input_blobs") or {},
        }
    definition = {
        "mode": row.get("mode"),
        "inputs": list(row.get("inputs") or ()),
        "definition_etag": row.get("definition_etag"),
    }
    located = worktree_git_dir(Path(worktree) if worktree is not None else ctx.store)
    if located is None:
        if verdict is None:
            return "missing"
        if verdict["passed"] is not True or verdict["check_etag"] != row["definition_etag"]:
            return "stale"
        return "fresh" if row.get("mode") in ("review", "human", "evidence") else "unknown"
    git_dir, worktree = located
    snapshot = read_index(git_dir, worktree)
    globs = list(row.get("inputs") or ())
    try:
        matched = match_inputs(snapshot, globs)
    except GlobError:
        return "unknown"
    stats = selected_stats(worktree, matched)
    return freshness(verdict, definition, snapshot, stats)


# ── verification modes ────────────────────────────────────────────────────


def _verify_one(
    ctx: Context,
    row: Mapping[str, Any],
    supplied: Mapping[str, Any],
    actor: str,
    worktree: Path | None,
    key: str | None,
) -> dict[str, Any]:
    mode = row["mode"]
    try:
        if mode == "cmd":
            payload = _cmd_payload(ctx, row, worktree)
        elif mode == "exists":
            payload = _exists_payload(row, worktree)
        elif mode == "evidence":
            payload = _evidence_payload(ctx, row)
        elif mode == "review":
            payload = _review_payload(ctx, row)
        elif mode == "human":
            payload = _human_payload(ctx, row, supplied, actor)
        else:  # pragma: no cover - parse_check pins the mode enum
            raise WriteRefused("VALIDATION_FAILED", f"unknown check mode {mode!r}")
    except GlobError as error:
        return {
            "check": row["slug"],
            "record": None,
            "passed": False,
            "freshness": "unknown",
            "reason": f"the declared inputs are not valid globs: {error}",
        }
    if mode == "cmd" and not payload["input_blobs"]:
        # A cmd verdict is only valid bound to its input blobs; a run that could
        # not name a committed input is reported as no proof and never recorded.
        return {
            "check": row["slug"],
            "record": None,
            "passed": False,
            "freshness": "missing",
            "reason": payload["reason"],
        }
    return _commit_verdict(ctx, row, payload, actor, key)


def _cmd_payload(
    ctx: Context, row: Mapping[str, Any], worktree: Path | None
) -> dict[str, Any]:
    spec = row["spec"]
    globs = [str(value) for value in spec.get("inputs") or ()]
    command = [str(value) for value in spec.get("cmd") or ()]
    timeout = float(spec.get("timeout_s") or DEFAULT_TIMEOUT_S)
    root = _git_worktree(worktree)
    if root is None:
        return _payload(
            row,
            input_globs=globs,
            reason="the project checkout is not a git worktree, so no input can be proven",
            runner_digest=EMPTY_DIGEST,
            exit=-1,
        )
    staged = git_runner.ls_files_staged(root, root)
    matched = _match(staged, globs)
    untracked, dirty = _status_paths(root)
    uncommitted = sorted(set(matched) & dirty)
    if uncommitted or _match(untracked, globs):
        return _payload(
            row,
            input_globs=globs,
            reason=_inputs_reason(matched, untracked, uncommitted, globs),
            runner_digest=EMPTY_DIGEST,
            exit=-1,
        )
    if not matched:
        return _payload(
            row,
            input_globs=globs,
            reason=(
                "no committed file matches the declared inputs "
                + ", ".join(globs)
            ),
            runner_digest=EMPTY_DIGEST,
            exit=-1,
        )
    blobs = {path: staged[path] for path in matched}
    try:
        ran = run_command(command, cwd=root, confine_to=root, timeout=timeout)
    except OSError as error:
        return _payload(
            row,
            input_globs=globs,
            input_blobs=blobs,
            reason=f"the command could not start: {error}",
            runner_digest=EMPTY_DIGEST,
            exit=-1,
        )
    after = git_runner.ls_files_staged(root, root)
    after_untracked, after_dirty = _status_paths(root)
    changed = sorted(
        {
            *[path for path in matched if after.get(path) != blobs[path]],
            *(set(matched) & after_dirty),
            *_match(after_untracked, globs),
        }
    )
    exit_code = ran["exit"] if ran["exit"] is not None else -1
    if changed:
        reason: str | None = (
            "the declared inputs changed while the check ran: " + ", ".join(changed)
        )
    elif ran["timed_out"]:
        reason = f"the command timed out after {timeout:g} s"
    elif exit_code != 0:
        reason = f"the command exited {exit_code}"
    else:
        reason = None
    return _payload(
        row,
        passed=reason is None,
        input_globs=globs,
        input_blobs=blobs,
        runner_digest=ran["digest"],
        exit=exit_code,
        reason=reason,
        candidate_commit=git_runner.rev_parse_head(root, root),
    )


def _exists_payload(row: Mapping[str, Any], worktree: Path | None) -> dict[str, Any]:
    spec = row["spec"]
    globs = [str(value) for value in spec.get("inputs") or ()]
    if worktree is None:
        return _payload(
            row,
            input_globs=globs,
            reason="the project checkout is not a worktree",
        )
    root = worktree.resolve()
    missing: list[str] = []
    for declared in spec.get("exists") or ():
        path = (root / str(declared)).resolve()
        if not path.is_relative_to(root):
            raise WriteRefused(
                "FORBIDDEN", f"{declared!r} leaves the worktree", {"path": declared}
            )
        if not path.exists():
            missing.append(str(declared))
    staged = (
        git_runner.ls_files_staged(root, root) if _git_worktree(worktree) else {}
    )
    matched = _match(staged, globs)
    blobs = {path: staged[path] for path in matched}
    reason = None if not missing else "missing path(s): " + ", ".join(sorted(missing))
    return _payload(
        row,
        passed=reason is None,
        input_globs=globs,
        input_blobs=blobs,
        reason=reason,
        candidate_commit=(
            git_runner.rev_parse_head(root, root) if _git_worktree(worktree) else None
        ),
    )


def _evidence_payload(ctx: Context, row: Mapping[str, Any]) -> dict[str, Any]:
    declared = [str(value) for value in row["spec"].get("evidence") or ()]
    unavailable = _unavailable_evidence(ctx, declared)
    reason = (
        None
        if not unavailable
        else "unavailable evidence record(s): " + ", ".join(unavailable)
    )
    return _payload(
        row, passed=reason is None, evidence=declared, reason=reason
    )


def _review_payload(ctx: Context, row: Mapping[str, Any]) -> dict[str, Any]:
    item = row["item"]
    review = query.review_for_item(ctx.index, item) if item else None
    if review is None:
        return _payload(
            row, reason="no separated reviewer verdict has been recorded for this Item"
        )
    data = review.get("data") or {}
    reason: str | None = None
    if review.get("verdict") != "approve":
        reason = f"the reviewer verdict is {review.get('verdict')!r}"
    else:
        separation = review_separation(
            str(data.get("implementer_actor") or ""),
            str(data.get("reviewer_actor") or review.get("reviewer") or ""),
            str(data.get("implementer_model") or ""),
            str(data.get("reviewer_model") or ""),
            _different_model_required(ctx),
        )
        if separation is not None:
            reason = separation
    return _payload(
        row,
        passed=reason is None,
        evidence=[review["record_id"]],
        reason=reason,
        candidate_commit=review.get("candidate_commit"),
    )


def _different_model_required(ctx: Context) -> bool:
    """Whether this project's charter requires the reviewer to use another model."""
    return True if ctx.policy is None else ctx.policy.reviewer_different_model


def _human_payload(
    ctx: Context,
    row: Mapping[str, Any],
    supplied: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    if actor != "human":
        raise WriteRefused(
            "PERMISSION_DENIED", "a human verdict needs actor 'human'", {"actor": actor}
        )
    verdict = supplied.get("verdict")
    if verdict not in HUMAN_VERDICTS:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a human Check needs --verdict pass or fail",
            {"check": row["slug"]},
        )
    action = "pass" if verdict == "pass" else "fail"
    reason = validate_confirmation(action, row["slug"], supplied.get("confirm"))
    if reason is not None:
        raise WriteRefused(
            "VALIDATION_FAILED",
            reason,
            {"check": row["slug"], "confirm": confirmation_phrase(action, row["slug"])},
        )
    evidence = [str(value) for value in supplied.get("evidence") or ()]
    if verdict == "fail" and not evidence:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a failed human verdict needs at least one evidence Record",
            {"check": row["slug"]},
        )
    unavailable = _unavailable_evidence(ctx, evidence)
    if unavailable:
        return _payload(
            row,
            evidence=evidence,
            reason="unavailable evidence record(s): " + ", ".join(unavailable),
        )
    return _payload(
        row,
        passed=verdict == "pass",
        evidence=evidence,
        reason=None if verdict == "pass" else "the human failed this check",
    )


def _commit_verdict(
    ctx: Context,
    row: Mapping[str, Any],
    payload: dict[str, Any],
    actor: str,
    key: str | None,
) -> dict[str, Any]:
    parse_verdict(payload)
    item = row["item"]
    about = [row["slug"], item] if item else [row["slug"]]
    result = commit(
        ctx.store,
        ctx.manifest,
        [],
        {"kind": "check.verdict", "about": about, "data": payload},
        actor=actor,
        key=None if key is None else f"{key}:{row['slug']}",
        index=ctx.index,
    )
    current = query.check_state(ctx.index, row["slug"]) or row
    return {
        "check": row["slug"],
        "record": result.get("record"),
        "passed": bool(payload["passed"]),
        "freshness": freshness_of(ctx, current),
        "reason": payload["reason"],
    }


def _payload(row: Mapping[str, Any], **values: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": 1,
        "check": row["slug"],
        "check_etag": row["definition_etag"],
        "passed": False,
        "mode": row["mode"],
        "input_blobs": {},
        "input_globs": [],
        "runner_digest": None,
        "exit": None,
        "evidence": [],
        "reason": None,
        "candidate_commit": None,
    }
    data.update(values)
    return data


# ── declaration helpers ───────────────────────────────────────────────────


def _frontmatter(preset: str, spec: Check, item: str | None) -> dict[str, Any]:
    front: dict[str, Any] = {
        "primitive": "check",
        "preset": preset,
        "schema": spec.schema,
        "mode": spec.mode,
        "acceptance": spec.acceptance,
    }
    if spec.mode == "cmd":
        front["cmd"] = list(spec.cmd)
        front["inputs"] = list(spec.inputs)
    elif spec.mode == "exists":
        front["exists"] = list(spec.exists)
        front["inputs"] = list(spec.inputs)
    elif spec.mode == "evidence":
        front["evidence"] = list(spec.evidence)
    if spec.timeout_s is not None:
        front["timeout_s"] = spec.timeout_s
    if spec.legacy is not None:
        front["legacy"] = dict(spec.legacy)
    if item is not None:
        front["verifies"] = [f"[[{item}]]"]
    return front


def _validate_definition(spec: Check) -> None:
    if spec.timeout_s is not None and spec.timeout_s > MAX_TIMEOUT_S:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"check.timeout_s must not exceed {MAX_TIMEOUT_S} seconds",
            {"timeout_s": spec.timeout_s},
        )
    if spec.mode == "evidence":
        for record_id in spec.evidence:
            if not _RECORD_ID.match(record_id):
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    f"{record_id!r} is not a Record id",
                    {"evidence": record_id},
                )
    for glob in spec.inputs:
        problem = invalid_glob(glob)
        if problem is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{glob!r} is not a valid input glob: {problem}",
                {"inputs": glob},
            )


def _is_acceptance(
    index: Any, slug: str, current: Any, spec: Check
) -> bool:
    if spec.acceptance:
        return True
    if current is None:
        return False
    row = query.check_state(index, slug)
    return bool(row and row["acceptance"])


def _require_item(index: Any, slug: str) -> None:
    row = index.conn.execute(
        "SELECT primitive FROM docs WHERE slug = ? AND broken = 0 LIMIT 1", (slug,)
    ).fetchone()
    if row is None:
        raise WriteRefused(
            "VALIDATION_FAILED", f"unknown check target {slug!r}", {"target": slug}
        )
    if row[0] != "item":
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a check verifies an Item, not a {row[0]!r}",
            {"target": slug},
        )


def _declared_response(store: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    written = result.get("written") or []
    path = written[0] if written else None
    value = None
    if path is not None:
        target = store / path
        if target.is_file():
            value = etag(target.read_bytes())
    return {
        "slug": Path(path).stem if path else result.get("record"),
        "path": path,
        "etag": value,
        "record": result.get("record"),
    }


def _check_slug(value: Any) -> str:
    text = _trimmed(value)
    if text is None or not _SLUG.match(text):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{value!r} is not a check slug", {"slug": value}
        )
    return text


def _trimmed(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_spec(value: Any) -> dict[str, Any]:
    text = _trimmed(value)
    if text is None:
        raise WriteRefused("VALIDATION_FAILED", "check needs a spec file")
    path = Path(text)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND", f"check spec {text!r} does not exist", {"spec_file": text}
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read check spec {text!r}: {exc}",
            {"spec_file": text},
        ) from None
    if size > MAX_SPEC_BYTES:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the check spec is larger than {MAX_SPEC_BYTES} bytes",
            {"spec_file": text},
        )
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND", f"check spec {text!r} does not exist", {"spec_file": text}
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read check spec {text!r}: {exc}",
            {"spec_file": text},
        ) from None
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_keys)
    except _DuplicateKey as error:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the check spec repeats the key {error.args[0]!r}",
            {"spec_file": text},
        ) from None
    except ValueError:
        raise WriteRefused(
            "VALIDATION_FAILED", "the check spec is not valid JSON", {"spec_file": text}
        ) from None
    if not isinstance(payload, dict):
        raise WriteRefused(
            "VALIDATION_FAILED", "the check spec must be a JSON object", {"spec_file": text}
        )
    return payload


def _spec_quiet(value: Any) -> dict[str, Any] | None:
    text = _trimmed(value)
    if text is None:
        return None
    try:
        raw = Path(text).read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name, value in pairs:
        if name in data:
            raise _DuplicateKey(name)
        data[name] = value
    return data


# ── proof helpers ─────────────────────────────────────────────────────────


def _match(paths: Iterable[str], globs: Sequence[str]) -> tuple[str, ...]:
    entries = {
        path: IndexEntry(
            path=path,
            oid="",
            mode=0,
            size=0,
            mtime_s=0,
            mtime_ns=0,
            stage=0,
            flags=0,
            unknown=None,
        )
        for path in paths
    }
    return match_inputs(IndexSnapshot(entries, None, 20, None, None, None), globs)


def _status_paths(root: Path) -> tuple[set[str], set[str]]:
    untracked: set[str] = set()
    dirty: set[str] = set()
    for entry in git_runner.status_porcelain(root, root):
        code = entry[:2]
        path = entry[3:] if len(entry) > 3 and entry[2] == " " else entry
        path = path.replace("\\", "/")
        if code == "??":
            untracked.add(path)
        else:
            dirty.add(path)
    return untracked, dirty


def _inputs_reason(
    matched: Sequence[str],
    untracked: Sequence[str],
    uncommitted: Sequence[str],
    globs: Sequence[str],
) -> str:
    parts: list[str] = []
    if uncommitted:
        parts.append("declared inputs have uncommitted changes: " + ", ".join(uncommitted))
    missing = _match(untracked, globs)
    if missing:
        parts.append("declared inputs are untracked: " + ", ".join(missing))
    if not matched and not missing:
        parts.append("no committed file matches the declared inputs " + ", ".join(globs))
    return "; ".join(parts) or "no committed file matches the declared inputs"


def _unavailable_evidence(ctx: Context, record_ids: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for record_id in record_ids:
        row = ctx.index.conn.execute(
            "SELECT 1 FROM docs WHERE primitive = 'record' AND slug = ? AND broken = 0",
            (record_id,),
        ).fetchone()
        if row is None:
            missing.append(record_id)
    return missing


# ── worktree resolution ───────────────────────────────────────────────────


def _canonical_worktree(ctx: Context) -> Path:
    located = worktree_git_dir(ctx.store)
    if located is not None:
        return located[1].resolve()
    return Path(ctx.store).parent.resolve()


def _git_worktree(worktree: Path | None) -> Path | None:
    """The Git worktree root for ``worktree``, or None when it is not one.

    Only a Git worktree can name the blob IDs a verdict is bound to; a plain
    directory still supports checks that need no such proof.
    """
    if worktree is None:
        return None
    located = worktree_git_dir(worktree)
    return None if located is None else located[1].resolve()


def _resolve_worktree(ctx: Context, value: Any) -> Path:
    canonical = _canonical_worktree(ctx)
    text = _trimmed(value)
    if text is None:
        return canonical
    candidate = Path(text)
    if not candidate.is_absolute():
        raise WriteRefused(
            "VALIDATION_FAILED", "worktree must be an absolute path", {"worktree": text}
        )
    resolved = candidate.resolve()
    if resolved == canonical or _registered_worktree(ctx, resolved):
        return resolved
    raise WriteRefused(
        "VALIDATION_FAILED",
        "worktree must be the project checkout or a registered dispatch worktree",
        {"worktree": text, "canonical": str(canonical)},
    )


def _registered_worktree(ctx: Context, candidate: Path) -> bool:
    folder = local_dir(ctx.store) / "dispatch"
    if not folder.is_dir():
        return False
    for path in folder.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        worktree = payload.get("worktree") if isinstance(payload, dict) else None
        if isinstance(worktree, str) and Path(worktree).resolve() == candidate:
            return True
    return False
