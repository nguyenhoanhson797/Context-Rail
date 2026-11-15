"""The work brief: what an agent in any tool reads before it works an item.

A pure read. It moves no status, opens no seat, takes no lock and writes no
record. It never launches git, never checks out working trees, and never hashes
source trees; candidate diff evidence comes from mutation-time snapshots.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.context import code_area, recipes, surface
from context_rail.context import results as result_context_mod
from context_rail.context.budget import item_budget
from context_rail.context.role_brief import acting, render_role, role_guide
from context_rail.context.seat_brief import seat_brief_for
from context_rail.model.candidate import DIFF_TEXT_CAP
from context_rail.model.codegraph import effective_code_graph
from context_rail.model.roles import Role
from context_rail.store import query
from context_rail.store.codegraph import has_snapshot, latest_commit, open_graph

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.model.cto import Profile

__all__ = [
    "DEFAULT_CHARS",
    "DIFF_CHARS",
    "REVIEW_SECTIONS",
    "WORK_BRIEF_SECTIONS",
    "build_work_brief",
    "reviews_work",
]

WORK_BRIEF_SECTIONS = (
    "role",
    "objective",
    "context",
    "workflow",
    "selected scope",
    "conventions",
    "constraints",
    "selected files",
    "checks",
    "allowed rail methods",
    "recording",
    "code area",
)
REVIEW_SECTIONS = ("diff", "evidence")
DIFF_CHARS = DIFF_TEXT_CAP
DEFAULT_CHARS = 12000
OBJECTIVE_CHARS = 6000
NAME_MODE_ROLES = ("implementer", "reviewer")
NO_SNAPSHOT = (
    "no code graph snapshot for this commit; run `rail ops codegraph refresh` to build one"
)
_NEIGHBOURS = 12
_FLOOR = 240
_TRIM_ORDER = (
    "diff",
    "code area",
    "context",
    "selected files",
    "checks",
    "evidence",
    "result",
    "workflow",
    "constraints",
    "conventions",
    "allowed rail methods",
    "role",
    "selected scope",
    "recording",
    "objective",
)
_WORKER_METHODS = (
    "find",
    "show",
    "brief",
    "ledger",
    "result",
    "candidate",
    "verify",
    "review",
    "report",
)


def reviews_work(ctx: Context, role: str) -> bool:
    """Whether a role reads a candidate or result to judge it rather than to change it."""
    if not ctx.role_mode:
        return role == "reviewer"
    found = ctx.pack.operating.roles.get(role)
    if found is None:
        return False
    can_review = "review" in found.can.methods
    sees_review_parts = "diff" in found.sees.parts or "evidence" in found.sees.parts
    return can_review or sees_review_parts


def build_work_brief(
    ctx: Context,
    *,
    item: str,
    role: str,
    commit: str | None,
    max_chars: int | None,
    candidate: dict[str, Any] | None = None,
    result_info: dict[str, Any] | None = None,
    granted_by: str = "human",
) -> dict[str, Any]:
    """The brief ``role`` would work ``item`` from, with every section bounded.

    ``candidate`` is the recorded candidate a reviewer judges (when any);
    ``result_info`` is the bounded producer result context (when any).
    The handler resolves both so this module never has to guess a revision.
    """
    shown = query.show(ctx.index, item)
    doc = shown["doc"]
    title = doc.get("title") or item
    body = shown["body"]
    pack_role = ctx.pack.operating.roles.get(role) if ctx.role_mode else None
    guide = role_guide(ctx, role)
    reviewing = reviews_work(ctx, role)
    checks = query.check_states(ctx.index, item)
    globs = sorted({glob for row in checks for glob in row["inputs"]})
    earlier = _earlier_files(ctx, item)
    compiled_results = result_context_mod.compiled_results_for_item(ctx, item)

    if result_info is None and compiled_results is not None:
        result_info = result_context_mod.result_context(
            ctx,
            item,
            include_body=reviewing,
            candidate_commit=candidate["commit"] if candidate is not None else None,
        )

    sections: dict[str, str] = {
        "objective": _clip(f"{title}\n\n{body}".strip(), OBJECTIVE_CHARS),
        "workflow": _workflow(ctx, item),
        "selected scope": ", ".join(_parents(ctx, item)) or "(none)",
        "conventions": _conventions(reviewing),
        "constraints": _constraints(ctx, item, pack_role, reviewing),
        "selected files": _selected_files(globs, candidate, reviewing),
        "checks": _check_lines(checks),
        "allowed rail methods": _methods(pack_role, guide.profile),
        "recording": _recording(item, reviewing, compiled_results=compiled_results),
    }
    extras: dict[str, str] = {}
    if reviewing:
        if candidate is not None:
            extras["diff"], extras["evidence"] = _review_parts(ctx, candidate, checks)
            sections["diff"] = extras["diff"]
            sections["evidence"] = extras["evidence"]
        elif result_info is not None:
            sections["evidence"] = _check_evidence(checks)
            extras["evidence"] = sections["evidence"]
        if result_info is not None and compiled_results is not None:
            fixed_meta, body_prose = result_context_mod.render_result_details(result_info)
            sections["result"] = fixed_meta + ("\n\n" + body_prose if body_prose else "")
            extras["results"] = result_context_mod.render_result_pointer_line(result_info)
    elif compiled_results is not None:
        contract_text = result_context_mod.render_worker_contract(
            compiled_results.produce, item
        )
        prior_lines = (
            result_context_mod.render_result_summaries(result_info["history"])
            if result_info and result_info.get("history")
            else []
        )
        worker_result = contract_text
        if prior_lines:
            worker_result += "\nprior results:\n" + "\n".join(
                f"- {line}" for line in prior_lines
            )
        sections["result"] = worker_result
        extras["results"] = worker_result

    area = _area_text(ctx, candidate, title=title, body=body, globs=globs, earlier=earlier)
    sections["code area"] = area
    extras["code_area"] = area
    total = _budget(pack_role, guide.profile, max_chars)
    sections["role"] = render_role(guide, limit=max(total // 3, _FLOOR))
    sections["context"] = _context(
        ctx,
        item,
        role,
        pack_role,
        shown,
        granted_by=granted_by,
        extras=extras,
        limit=max(total // 3, _FLOOR),
    )

    extra_sections = tuple(
        name for name in (*REVIEW_SECTIONS, "result") if name in sections
    )
    order = (*WORK_BRIEF_SECTIONS, *extra_sections)
    fitted, omitted = _fit({name: sections[name] for name in order}, total)
    text = "\n\n".join(f"## {name}\n{fitted[name]}" for name in order if name in fitted)
    result_payload: dict[str, Any] = {
        "item": item,
        "role": role,
        "acting": acting(guide),
        "candidate": None if candidate is None else candidate["record_id"],
        "sections": fitted,
        "text": text,
        "estimated_tokens": recipes.estimate_tokens(text),
        "omitted": omitted,
    }
    if result_info is not None:
        selected = result_info.get("selected")
        result_payload["result"] = None if selected is None else selected.get("record_id")
        result_payload["result_state"] = {
            "contract_enabled": bool(result_info.get("contract_enabled")),
            "current": bool(result_info.get("current")),
            "completed": bool(result_info.get("completed")),
            "reasons": list(result_info.get("reasons") or ()),
            "status": (
                "completed"
                if result_info.get("completed")
                else "current"
                if result_info.get("current")
                else "missing"
                if selected is None
                else "invalid"
            ),
        }
    return result_payload


def _budget(role: Role | None, profile: Profile | None, max_chars: int | None) -> int:
    total = DEFAULT_CHARS
    if role is not None:
        total = role.sees.chars
    if profile is not None:
        total = min(total, int(profile.budget.get("max_chars", total)))
    if max_chars is not None:
        # An explicit budget is the budget; the floor only stands in for a
        # default the caller never chose.
        return min(total, max_chars)
    return max(total, _FLOOR)


def _fit(sections: Mapping[str, str], budget: int) -> tuple[dict[str, str], dict[str, int]]:
    """Shorten sections, least important first, until the whole fits ``budget``."""
    fitted = dict(sections)
    omitted: dict[str, int] = {}
    floor = min(_FLOOR, budget)

    def size() -> int:
        return sum(len(name) + len(text) + 5 for name, text in fitted.items()) + 2 * len(fitted)

    for name in _TRIM_ORDER:
        over = size() - budget
        if over <= 0:
            break
        text = fitted.get(name)
        if text is None:
            continue
        if name == "result":
            trimmed_result = _trim_result_section(text, over, floor)
            if trimmed_result is not None and len(trimmed_result) < len(text):
                omitted[name] = len(text) - len(trimmed_result)
                fitted[name] = trimmed_result
            continue
        if len(text) <= floor:
            continue
        keep = max(floor, len(text) - over - 1)
        fitted[name] = text[:keep].rstrip() + "…"
        omitted[name] = len(text) - keep
    order = [name for name in _TRIM_ORDER if name in fitted]
    while size() > budget and order:
        name = order.pop(0)
        if name == "result" and len(order) > 0:
            # Prefer dropping non-pinned sections before dropping the visible result identity/pointer.
            non_result = [candidate for candidate in order if candidate != "result"]
            if non_result:
                order.remove(non_result[0])
                order.insert(0, "result")
                name = non_result[0]
        if name == "result":
            pointer = next(
                (
                    line
                    for line in fitted["result"].splitlines()
                    if line.startswith("result:")
                ),
                "",
            )
            if pointer and len(pointer) + 12 <= budget:
                fitted["result"] = pointer
                omitted["result"] = max(
                    omitted.get("result", 0), len(sections["result"]) - len(pointer)
                )
                if size() <= budget:
                    break
        del fitted[name]
        omitted[name] = len(sections[name])
    return fitted, omitted


def _trim_result_section(text: str, over: int, floor: int) -> str | None:
    """Clip result body prose while preserving exact identity and read-pointer lines."""
    lines = text.splitlines()
    pinned_prefixes = result_context_mod.PINNED_RESULT_PREFIXES
    pinned = [line for line in lines if line.startswith(pinned_prefixes)]
    if not pinned:
        return None
    pinned_block = "\n".join(pinned)
    prose_lines = [line for line in lines if not line.startswith(pinned_prefixes)]
    prose = "\n".join(prose_lines).strip()
    if not prose:
        return pinned_block if len(pinned_block) < len(text) else None
    notice = "[clipped evidence; read full result via pointer above]"
    target_len = max(len(pinned_block), len(text) - over)
    if target_len <= len(pinned_block) + len(notice) + 2:
        if len(pinned_block) + len(notice) + 1 < len(text):
            return f"{pinned_block}\n{notice}"
        return pinned_block
    budget_for_prose = max(0, target_len - len(pinned_block) - 2)
    if budget_for_prose < len(notice) + 12:
        return f"{pinned_block}\n{notice}"
    trim_at = max(0, budget_for_prose - len(notice) - 1)
    clipped_prose = prose[:trim_at].rstrip()
    return f"{pinned_block}\n\n{clipped_prose}\n{notice}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _workflow(ctx: Context, item: str) -> str:
    try:
        return surface.render_card(surface.card(ctx, item))
    except surface.SurfaceError:
        return "(no workflow card)"


def _parents(ctx: Context, item: str) -> list[str]:
    return [
        dst
        for (dst,) in ctx.index.conn.execute(
            "SELECT r.dst FROM relations r JOIN docs d ON d.path = r.src_path "
            "WHERE d.slug = ? AND r.rel = 'part_of' ORDER BY r.dst",
            (item,),
        )
    ]


def _conventions(reviewing: bool) -> str:
    if reviewing:
        return (
            "Judge the candidate commit against the item's acceptance and the "
            "charter, not against the implementer's description of the work."
        )
    return "Follow the repository's own conventions."


def _constraints(
    ctx: Context, item: str, role: Role | None, reviewing: bool
) -> str:
    lines: list[str] = []
    state = item_budget(ctx.index, ctx.policy, item)
    if state.state != "ok":
        lines.append(
            f"Budget: {state.state}"
            + (f" ({'; '.join(state.reasons)})" if state.reasons else "")
            + ". A budget only warns; it never refuses work."
        )
    if reviewing:
        lines.append(
            "Read-only review: do not edit or commit. Approve exactly the candidate "
            "commit, or reject it with findings the implementer can act on."
        )
    else:
        lines.append(
            "Never stage files to make a Check pass, and never widen your own "
            "tools, autonomy or budget."
        )
    if role is not None:
        lines.append(f"Code write reach: {_code_reach(role)}. Never change a file outside it.")
        if role.workspace != "none":
            lines.append(f"Workspace: {role.workspace}.")
    policy = ctx.policy
    if policy is not None and policy.gates:
        needed = _gate_needs(policy)
        if needed:
            lines.append("Done needs: " + ", ".join(needed) + ".")
    return "\n".join(lines) or "(none)"


def _gate_needs(policy: Any) -> list[str]:
    needed: list[str] = []
    gate = policy.gates.get("item_done")
    if gate is None:
        return needed
    if getattr(gate, "require_checks", False):
        needed.append("passing Checks")
    if getattr(gate, "require_review", False):
        needed.append("a separated review of the final commit")
    if getattr(gate, "require_human", False):
        needed.append("a human sign-off")
    return needed


def _code_reach(role: Role) -> str:
    return ", ".join(pattern.raw for pattern in role.code_write) or "(none)"


def _selected_files(
    globs: Sequence[str], candidate: dict[str, Any] | None, reviewing: bool
) -> str:
    declared = ", ".join(globs) or "(none declared)"
    if reviewing and candidate is not None:
        changed = ", ".join(candidate["data"].get("files_changed") or ()) or "(none captured)"
        return f"changed: {changed}\ncheck inputs: {declared}"
    return declared


def _check_lines(checks: Sequence[dict[str, Any]]) -> str:
    return "\n".join(f"- {row['slug']}" for row in checks) or "(none declared)"


def _methods(pack_role: Role | None, profile: Profile | None) -> str:
    if pack_role is not None:
        allowed = sorted(key for key, value in pack_role.can.methods.items() if value)
        return ", ".join(allowed) or "(read only)"
    if profile is not None:
        return ", ".join(profile.allowed_methods)
    return ", ".join(_WORKER_METHODS)


def _recording(
    item: str, reviewing: bool, *, compiled_results: Any = None
) -> str:
    if reviewing:
        lines = [
            "Record your verdict with the rail; never edit the store files yourself.",
            f"1. rail check run {item}   (add --worktree <path> to run in your own checkout)",
        ]
        if compiled_results is not None:
            lines.append(
                f"2. rail loop review {item} [--commit <sha>] [--result <record-id>] "
                "--verdict approve|reject --notes-file <file> [--set KEY=VALUE]"
            )
        else:
            lines.append(
                f"2. rail loop review {item} --commit <sha> --verdict approve|reject "
                "[--notes-file <file>]"
            )
        lines.append(
            f"3. rail loop report {item} --report-file <file>   (what you found, or nothing to report)"
        )
        return "\n".join(lines)
    lines = [
        "Work in your own checkout. Record results with the rail; never edit the "
        "store files yourself.",
    ]
    if compiled_results is not None:
        lines.extend(
            (
                "1. For code changes, commit your change and record a candidate first.",
                f"2. rail loop candidate {item} --commit <sha> [--model <name>] "
                "[--harness <tool>] [--usage <counter>=<n>] [--duration <seconds>]",
                f"3. rail loop result {item} --body-file <file> [--commit <sha>] "
                "[--outcome completed|partial|blocked|failed] [--set KEY=VALUE]",
                f"4. rail check run {item}   (add --worktree <path> to run in your own checkout)",
                f"5. rail change move {item} <status> --expect <etag>   (when the workflow allows it)",
                f"6. rail loop report {item} --report-file <file>   (what you found, or nothing to report)",
            )
        )
    else:
        lines.extend(
            (
                "1. Commit your change.",
                f"2. rail loop candidate {item} --commit <sha> [--model <name>] "
                "[--harness <tool>] [--usage <counter>=<n>] [--duration <seconds>]",
                f"3. rail check run {item}   (add --worktree <path> to run in your own checkout)",
                f"4. rail change move {item} <status> --expect <etag>   (when the workflow allows it)",
                f"5. rail loop report {item} --report-file <file>   (what you found, or nothing to report)",
            )
        )
    return "\n".join(lines)


def _check_evidence(checks: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in checks:
        state = "no verdict yet"
        if row["verdict"] is not None:
            state = f"{'passed' if row['passed'] else 'failed'}; record {row['verdict']}"
        lines.append(f"- {row['slug']} ({row['mode']}): {state}")
    return "\n".join(lines) or "(none declared)"


def _review_parts(
    ctx: Context, candidate: dict[str, Any], checks: Sequence[dict[str, Any]]
) -> tuple[str, str]:
    return _diff(ctx, candidate), _check_evidence(checks)


def _diff(ctx: Context, candidate: dict[str, Any]) -> str:
    data = candidate.get("data") or {}
    files = list(data.get("files_changed") or ())
    reach = getattr(ctx, "reach", None)
    if reach is not None and files:
        denied = [path for path in files if not reach.can_read_code(path)]
        if denied:
            return (
                "(diff hidden: changed files are outside this seat's code read reach; "
                f"commit {candidate['commit'][:12]})"
            )
    if "diff" not in data:
        return (
            f"(patch snapshot unavailable for historical candidate {candidate['record_id']}; "
            f"read external diff with `git diff {candidate['base'][:12]} {candidate['commit'][:12]}`)"
        )
    text = data.get("diff")
    if not isinstance(text, str) or not text.strip():
        return "(no changed lines)"
    cut = bool(data.get("diff_truncated", False))
    notice = ""
    if cut:
        notice = (
            f"\n(diff cut at {DIFF_CHARS} characters; read the rest with "
            f"`git diff {candidate['base'][:12]} {candidate['commit'][:12]}`)"
        )
    return "```diff\n" + text.rstrip() + "\n```" + notice


def _earlier_files(ctx: Context, item: str) -> list[str]:
    files: list[str] = []
    for row in query.candidates_for(ctx.index, item, limit=10):
        for path in row["data"].get("files_changed") or ():
            if path not in files:
                files.append(path)
    return files


def _area_text(
    ctx: Context,
    candidate: dict[str, Any] | None,
    *,
    title: str,
    body: str,
    globs: Sequence[str],
    earlier: Sequence[str],
) -> str:
    manifest_graph = ctx.manifest.code_graph
    if effective_code_graph(manifest_graph, ctx.pack.policy.get("code_graph")) == "none":
        return "(none)"
    store = Path(ctx.store)
    base = candidate["base"] if candidate is not None else latest_commit(store)
    if base is None or not has_snapshot(store, base):
        return NO_SNAPSHOT
    graph = open_graph(store, base)
    if graph is None:
        return NO_SNAPSHOT
    with graph:
        area = code_area.predict_area(
            graph, text=f"{title}\n{body}", globs=globs, earlier=earlier
        )
    return code_area.render_area(
        graph_line=f"graph: snapshot @{base[:7]}", area=area, history=earlier
    )


def _context(
    ctx: Context,
    item: str,
    role: str,
    pack_role: Role | None,
    shown: Mapping[str, Any],
    *,
    granted_by: str,
    extras: Mapping[str, str],
    limit: int,
) -> str:
    if pack_role is not None:
        brief = seat_brief_for(
            ctx,
            role_name=role,
            anchor=item,
            profile=None,
            granted_by=granted_by,
            extra_parts=extras,
            max_chars=limit,
            with_role=False,
        )
        return brief["text"]
    return _neighbourhood(ctx, shown)


def _neighbourhood(ctx: Context, shown: Mapping[str, Any]) -> str:
    titles = recipes.item_titles(ctx.index)
    lines: list[str] = []
    for label, groups in (("out", shown["out"]), ("in", shown["in"])):
        for relation, slugs in groups.items():
            for slug in slugs[:_NEIGHBOURS]:
                lines.append(f"- {label} {relation}: {slug} ({titles.get(slug, slug)})")
    return "\n".join(lines[: 2 * _NEIGHBOURS]) or "(no related documents)"
