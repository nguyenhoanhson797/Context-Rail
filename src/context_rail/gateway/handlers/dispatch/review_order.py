"""The order a reviewer reads: the Item, the candidate's bounded diff and the
Check evidence, capped so a large candidate still fits one prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from context_rail.gateway.handlers.common import bounded, checkout, read_doc, skill_pins
from context_rail.model.cto import Profile
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner.droid import extract_block
from context_rail.store import query
from context_rail.store.doc import etag

from .checkpoint import _git
from .common import REVIEW_DIFF_MAX, REVIEW_VERDICTS, _COMMIT, _string_list

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def _review_metadata(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    candidate: str,
    profile_name: str,
    profile: Profile,
    profile_etag: str,
    paths: local_dispatch.DispatchPaths,
) -> dict[str, Any]:
    data, _doc = read_doc(ctx.store, rel)
    return {
        "schema": 1,
        "dispatch_id": paths.worktree.name,
        "item": slug,
        "item_etag": etag(data),
        "actor": profile_name,
        "profile": profile_name,
        "profile_version": profile.version,
        "profile_etag": profile_etag,
        "skills": skill_pins(ctx, profile),
        "base_commit": candidate,
        "candidate_commit": candidate,
        "checks": [row["slug"] for row in query.check_states(ctx.index, slug)],
        "store": str(Path(ctx.store).absolute()),
        "worktree": str(paths.worktree.absolute()),
    }


def _review_sections(
    ctx: Context,
    *,
    slug: str,
    rel: str,
    run: dict[str, Any],
    candidate: str,
    profile: Profile,
) -> dict[str, str]:
    _data, doc = read_doc(ctx.store, rel)
    title = doc.title or slug
    scope = ", ".join(doc.relations.get("part_of", ())) or "(none)"
    changed = ", ".join(run["data"].get("files_changed") or ()) or "(none captured)"
    methods = ", ".join(profile.allowed_methods)
    return {
        "objective": bounded(
            f"Review candidate {candidate} for {title}.\n\n{doc.body}", 6000
        ),
        "selected scope": scope,
        "conventions": (
            "Judge the candidate commit against the Item's acceptance and the "
            "charter, not against the implementer's description of the work."
        ),
        "constraints": (
            "Read-only review: do not edit, commit, run commands or merge. HEAD must "
            "stay at the candidate commit. Approve exactly this commit, or reject it "
            "with findings the implementer can act on."
        ),
        "selected files": "\n".join(
            part for part in (changed, _bounded_diff(ctx, run, candidate)) if part
        ),
        "checks": _check_evidence(ctx, slug),
        "allowed rail methods": methods,
        "no-store-edit rule": (
            f"Never write {Path(ctx.store).absolute()} (the canonical store). Read it "
            f"with `rail --store {Path(ctx.store).absolute()}`."
        ),
        "commit expectation": (
            "Leave the worktree untouched: the parent records your verdict; you never "
            "merge, rebase or push."
        ),
        "final report protocol": (
            "End your text with exactly one fenced ```rail-report block and exactly one "
            "fenced ```rail-review block holding one JSON object with schema, verdict "
            "(approve or reject), candidate_commit, findings and evidence. Prose never "
            "stands in for a Check."
        ),
    }


def _bounded_diff(ctx: Context, run: dict[str, Any], candidate: str) -> str:
    """The reviewer's bounded view of what the candidate changed.

    The implementer's dispatch progress names the commit the candidate branch
    started from; when it is gone, the candidate's own commit is shown instead.
    """
    base = None
    dispatch_id = run.get("dispatch_id")
    if isinstance(dispatch_id, str) and dispatch_id:
        progress = local_dispatch.read_progress(Path(ctx.store), dispatch_id) or {}
        base = progress.get("base_commit")
    root, _git_dir = checkout(ctx, action="a dispatch")
    if isinstance(base, str) and _COMMIT.match(base):
        result = _git(["diff", "--no-color", f"{base}..{candidate}"], root)
    else:
        result = _git(["show", "--no-color", "--format=commit %H", candidate], root)
    if result["exit"] != 0:
        return "(the parent could not produce a bounded diff)"
    text = result["stdout"].strip()
    if not text:
        return "(no changed lines captured)"
    return "```diff\n" + bounded(text, REVIEW_DIFF_MAX) + "\n```"


def _check_evidence(ctx: Context, slug: str) -> str:
    lines: list[str] = []
    for row in query.check_states(ctx.index, slug):
        state = "no verdict yet"
        if row["verdict"] is not None:
            state = (
                f"{'passed' if row['passed'] else 'failed'}; record {row['verdict']}"
            )
        lines.append(f"- {row['slug']} ({row['mode']}): {state}")
    return "\n".join(lines) or "(none declared)"


def _review_outcome(ran: Any, candidate: str) -> dict[str, Any]:
    """The parsed verdict, or an invalid outcome that can never approve."""
    invalid = {"verdict": None, "findings": [], "evidence": [], "reason": None}
    if ran.failure:
        return {**invalid, "reason": ran.failure}
    block = extract_block(ran.text, "rail-review")
    if block is None:
        return {
            **invalid,
            "reason": "the review printed no single valid rail-review block",
        }
    schema = block.get("schema")
    if schema is None:
        return {**invalid, "reason": "the review block names no schema"}
    if isinstance(schema, bool) or schema != 1:
        return {**invalid, "reason": f"the review schema {schema!r} is not supported"}
    verdict = block.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        return {
            **invalid,
            "reason": f"the review verdict {verdict!r} is not approve or reject",
        }
    bound = block.get("candidate_commit")
    if not isinstance(bound, str) or not bound:
        return {**invalid, "reason": "the review block names no candidate commit"}
    if bound != candidate:
        return {
            **invalid,
            "reason": f"the review names candidate {bound!r}, not {candidate!r}",
        }
    findings = _string_list(block.get("findings", []), "review findings")
    evidence = _string_list(block.get("evidence", []), "review evidence")
    if findings is None or evidence is None:
        return {
            **invalid,
            "reason": "the review findings and evidence must be lists of strings",
        }
    return {
        "verdict": verdict,
        "findings": findings,
        "evidence": evidence[:20],
        "reason": None,
    }
