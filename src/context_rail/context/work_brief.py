"""The work brief: what an agent in any tool reads before it works an item.

A pure read. It moves no status, opens no seat, takes no lock and writes no
record. Git is read only for committed objects (a revision, a merge base, a
diff); the working tree is never hashed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.context import code_area, recipes, surface
from context_rail.context.budget import item_budget
from context_rail.context.seat_brief import seat_brief_for
from context_rail.model.codegraph import effective_code_graph
from context_rail.model.roles import Role
from context_rail.store import query
from context_rail.store.codegraph import has_snapshot, open_graph

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = [
    "DEFAULT_CHARS",
    "DIFF_CHARS",
    "REVIEW_SECTIONS",
    "WORK_BRIEF_SECTIONS",
    "build_work_brief",
    "reviews_work",
]

WORK_BRIEF_SECTIONS = (
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
DIFF_CHARS = 6000
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
    "workflow",
    "constraints",
    "conventions",
    "allowed rail methods",
    "selected scope",
    "recording",
    "objective",
)
_WORKER_METHODS = (
    "find",
    "show",
    "brief",
    "ledger",
    "candidate",
    "verify",
    "review",
    "report",
)


def reviews_work(ctx: Context, role: str) -> bool:
    """Whether a role reads a candidate to judge it rather than to change it."""
    if not ctx.role_mode:
        return role == "reviewer"
    found = ctx.pack.operating.roles.get(role)
    return found is not None and "diff" in found.sees.parts


def build_work_brief(
    ctx: Context,
    *,
    item: str,
    role: str,
    commit: str | None,
    max_chars: int | None,
    candidate: dict[str, Any] | None = None,
    granted_by: str = "human",
) -> dict[str, Any]:
    """The brief ``role`` would work ``item`` from, with every section bounded.

    ``candidate`` is the recorded candidate a reviewer judges; the handler
    resolves it so this module never has to guess a revision.
    """
    shown = query.show(ctx.index, item)
    doc = shown["doc"]
    title = doc.get("title") or item
    body = shown["body"]
    pack_role = ctx.pack.operating.roles.get(role) if ctx.role_mode else None
    reviewing = reviews_work(ctx, role)
    checks = query.check_states(ctx.index, item)
    globs = sorted({glob for row in checks for glob in row["inputs"]})
    earlier = _earlier_files(ctx, item)

    sections: dict[str, str] = {
        "objective": _clip(f"{title}\n\n{body}".strip(), OBJECTIVE_CHARS),
        "workflow": _workflow(ctx, item),
        "selected scope": ", ".join(_parents(ctx, item)) or "(none)",
        "conventions": _conventions(reviewing),
        "constraints": _constraints(ctx, item, pack_role, reviewing),
        "selected files": _selected_files(globs, candidate, reviewing),
        "checks": _check_lines(checks),
        "allowed rail methods": _methods(ctx, role, pack_role),
        "recording": _recording(item, reviewing),
    }
    extras: dict[str, str] = {}
    if reviewing and candidate is not None:
        extras["diff"], extras["evidence"] = _review_parts(ctx, candidate, checks)
        sections["diff"] = extras["diff"]
        sections["evidence"] = extras["evidence"]
    area = _area_text(ctx, candidate, title=title, body=body, globs=globs, earlier=earlier)
    sections["code area"] = area
    extras["code_area"] = area
    total = _budget(ctx, pack_role, max_chars)
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

    order = (*WORK_BRIEF_SECTIONS, *(name for name in REVIEW_SECTIONS if name in sections))
    fitted, omitted = _fit({name: sections[name] for name in order}, total)
    text = "\n\n".join(f"## {name}\n{fitted[name]}" for name in order if name in fitted)
    return {
        "item": item,
        "role": role,
        "candidate": None if candidate is None else candidate["record_id"],
        "sections": fitted,
        "text": text,
        "estimated_tokens": recipes.estimate_tokens(text),
        "omitted": omitted,
    }


def _budget(ctx: Context, role: Role | None, max_chars: int | None) -> int:
    total = DEFAULT_CHARS
    if role is not None:
        total = role.sees.chars
    profile = ctx.profile
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
        if text is None or len(text) <= floor:
            continue
        keep = max(floor, len(text) - over - 1)
        fitted[name] = text[:keep].rstrip() + "…"
        omitted[name] = len(text) - keep
    order = [name for name in _TRIM_ORDER if name in fitted]
    while size() > budget and order:
        name = order.pop(0)
        del fitted[name]
        omitted[name] = len(sections[name])
    return fitted, omitted


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
    return (
        "Follow the repository's own conventions. Write the failing test first, "
        "watch it fail for the right reason, then make it pass."
    )


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


def _methods(ctx: Context, role: str, pack_role: Role | None) -> str:
    if pack_role is not None:
        allowed = sorted(key for key, value in pack_role.can.methods.items() if value)
        return ", ".join(allowed) or "(read only)"
    profile = ctx.profile
    if profile is not None:
        return ", ".join(profile.allowed_methods)
    return ", ".join(_WORKER_METHODS)


def _recording(item: str, reviewing: bool) -> str:
    if reviewing:
        return "\n".join(
            (
                "Record your verdict with the rail; never edit the store files yourself.",
                f"1. rail check run {item}   (add --worktree <path> to run in your own checkout)",
                f"2. rail loop review {item} --commit <sha> --verdict approve|reject "
                "[--notes-file <file>]",
                f"3. rail loop report {item} --report-file <file>   (what you found, or nothing to report)",
            )
        )
    return "\n".join(
        (
            "Work in your own checkout. Record results with the rail; never edit the "
            "store files yourself.",
            "1. Commit your change.",
            f"2. rail loop candidate {item} --commit <sha> [--model <name>] "
            "[--harness <tool>] [--usage <counter>=<n>] [--duration <seconds>]",
            f"3. rail check run {item}   (add --worktree <path> to run in your own checkout)",
            f"4. rail change move {item} <status> --expect <etag>   (when the workflow allows it)",
            f"5. rail loop report {item} --report-file <file>   (what you found, or nothing to report)",
        )
    )


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
    from context_rail.gateway.handlers.common import checkout
    from context_rail.runner import git
    from context_rail.store.write import WriteRefused

    try:
        root, _git_dir = checkout(ctx, action="reading a candidate's diff")
        text, cut = git.diff_text(root, candidate["base"], candidate["commit"], DIFF_CHARS)
    except WriteRefused as refusal:
        return f"(the diff could not be read: {refusal.message})"
    if not text.strip():
        return "(no changed lines)"
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
    base = candidate["base"] if candidate is not None else _head(ctx)
    store = Path(ctx.store)
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


def _head(ctx: Context) -> str | None:
    from context_rail.gateway.handlers.common import checkout
    from context_rail.runner import git
    from context_rail.store.write import WriteRefused

    try:
        root, _git_dir = checkout(ctx, action="reading the base commit")
        return git.resolve_commit(root, "HEAD")
    except WriteRefused:
        return None


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
