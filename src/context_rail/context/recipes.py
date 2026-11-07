"""Indexed selections and bounded renderers behind the computed CTO views.

Everything here reads the index plus the protected store files one approval
hash covers. Nothing executes a command, hashes a source tree or scans the
store, and every list is bounded with its omissions counted.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from context_rail.kernel import budgets, structure
from context_rail.model.cto import USAGE_KEYS
from context_rail.store import query

if TYPE_CHECKING:
    from collections.abc import Sequence

    from context_rail.gateway.gateway import Context
    from context_rail.model.pack import Pack

BRIEF_CHARS = 12000
BRIEF_MAX_CHARS = 32000
BRIEF_CHARS_BOUNDS = (100, BRIEF_MAX_CHARS)
BRIEF_LIMIT = 20
BRIEF_LIMIT_BOUNDS = (1, 100)
DIGEST_LIMIT_BOUNDS = (1, 100)
STUCK_HOURS_DEFAULT = 24.0
NEAR_RATIO_DEFAULT = 0.8
CHARTER_TARGET = "charter"
EXCERPT_CHARS = 160
SECTION_KEYS = (
    "approval",
    "goal",
    "charter",
    "workflows",
    "reports",
    "executable",
    "active",
    "quality",
    "reversible",
    "conventions",
)
_SECTION_TITLES = {
    "approval": "Approval and human waits",
    "goal": "Goal and scope",
    "charter": "Charter gates, rights and budgets",
    "workflows": "Workflows and their next actions",
    "reports": "Reports to triage",
    "executable": "Executable items and dependencies",
    "active": "Active, stuck and too-long items",
    "quality": "Quality: runs, checks and reviews",
    "reversible": "Reversible decisions",
    "conventions": "Conventions and memory",
}
_PRIORITIES = ("must", "should", "could", "wont")


@dataclass
class Section:
    """One brief section: its pinned title, kept lines and trimmable entries."""

    key: str
    fixed: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return _SECTION_TITLES[self.key]

    def render(self) -> str:
        lines = [f"## {self.title}", *self.fixed]
        lines.extend(f"- {entry}" for entry in self.entries)
        return "\n".join(lines)


# ── shared selections ─────────────────────────────────────────────────────


def item_rows(ix: Any, pack: Pack) -> list[structure.ItemRow]:
    """Every Item with its part_of/depends_on edges and newest activity."""
    edges: dict[str, dict[str, list[str]]] = {}
    for src, rel, dst in ix.conn.execute(
        "SELECT r.src, r.rel, r.dst FROM relations r JOIN docs d ON d.path = r.src_path "
        "WHERE d.primitive = 'item' AND d.broken = 0 "
        "AND r.rel IN ('part_of', 'depends_on')"
    ):
        edges.setdefault(src, {}).setdefault(rel, []).append(dst)
    activity = query.last_activity_ms(ix)
    rows = ix.conn.execute(
        "SELECT slug, category, status, preset FROM docs "
        "WHERE primitive = 'item' AND broken = 0"
    ).fetchall()
    result: list[structure.ItemRow] = []
    for slug, category, status, preset in rows:
        declared = pack.presets.get(preset or "")
        workflow = pack.workflows[
            declared.workflow if declared is not None else "default"
        ]
        result.append(
            structure.ItemRow(
                slug=slug,
                category=category,
                part_of=_ordered(edges.get(slug, {}).get("part_of", ())),
                depends_on=_ordered(edges.get(slug, {}).get("depends_on", ())),
                last_activity_ms=activity.get(slug),
                dispatchable=workflow.dispatchable(status or ""),
                preset=preset or None,
                workflow=workflow.name,
                container=declared.container if declared is not None else None,
                status=status,
            )
        )
    return result


def _ordered(values: Sequence[str]) -> tuple[str, ...]:
    """One edge list as a tuple; a list that short is already in order."""
    return tuple(values) if len(values) < 2 else tuple(sorted(values))


def item_titles(ix: Any) -> dict[str, str]:
    """The title each Item slug carries, for display only."""
    return {
        slug: title or slug
        for slug, title in ix.conn.execute(
            "SELECT slug, title FROM docs WHERE primitive = 'item' AND broken = 0"
        )
    }


def stuck_window_ms(context: Context) -> int:
    """The stuck window the approved charter set, else the pack's own policy."""
    policy = context.policy
    if policy is not None:
        hours = policy.thresholds.get("stuck_hours")
    else:
        hours = context.pack.policy.get("stuck_hours")
    return int(float(hours if hours is not None else STUCK_HOURS_DEFAULT) * 3600 * 1000)


def stuck_rows(
    items: Sequence[structure.ItemRow],
    now_ms: int,
    window_ms: int,
    titles: dict[str, str],
) -> list[dict[str, Any]]:
    """Every stuck Item, quietest first."""
    by_slug = {item.slug: item for item in items}
    rows: list[dict[str, Any]] = []
    for slug in structure.stuck(list(items), now_ms, window_ms):
        item = by_slug[slug]
        quiet = None
        if item.last_activity_ms is not None:
            quiet = max(now_ms - item.last_activity_ms, 0) / 1000
        rows.append(
            {
                "slug": slug,
                "title": titles.get(slug, slug),
                "category": item.category,
                "last_activity": None
                if item.last_activity_ms is None
                else _iso(item.last_activity_ms),
                "quiet_s": quiet,
                "quiet_hours": None if quiet is None else round(quiet / 3600, 1),
            }
        )
    rows.sort(key=lambda row: (-(row["quiet_s"] or 0), row["slug"]))
    return rows


def too_long_rows(
    context: Context,
    items: Sequence[structure.ItemRow],
    titles: dict[str, str],
) -> list[dict[str, Any]]:
    """Every open Item whose worker attempts or runtime exceed the item budget."""
    policy = context.policy
    caps: dict[str, Any] = {} if policy is None else policy.budgets
    totals = query.worker_totals(context.index)
    if not totals:
        # No worker run means no attempts and no seconds, and a cap is only
        # exceeded above it, so nothing can be too long.
        return []
    facts = [
        budgets.ItemFacts(
            slug=item.slug,
            category=item.category or "not_started",
            last_activity_s=(item.last_activity_ms or 0) / 1000,
            dependencies=item.depends_on,
        )
        for item in items
    ]
    runs = [
        budgets.RunFacts(
            item=slug,
            role="implementer",
            attempts=measured["attempts"],
            seconds=measured["seconds"],
            usage=(),
        )
        for slug, measured in sorted(totals.items())
    ]
    near_ratio = NEAR_RATIO_DEFAULT
    if policy is not None:
        near_ratio = float(policy.thresholds.get("near_budget_ratio", NEAR_RATIO_DEFAULT))
    item_caps = caps.get("item", {}) if isinstance(caps, dict) else {}
    cap = budgets.Budget(
        attempts=item_caps.get("attempts"),
        seconds=item_caps.get("seconds"),
        tokens=item_caps.get("tokens"),
        credits=item_caps.get("credits"),
    )
    rows: list[dict[str, Any]] = []
    for slug in budgets.too_long(facts, runs, caps):
        measured = totals.get(slug, {"attempts": 0, "seconds": 0.0})
        own = [run for run in runs if run.item == slug]
        state = budgets.budget_state(own, cap, near_ratio)
        rows.append(
            {
                "slug": slug,
                "title": titles.get(slug, slug),
                "attempts": measured["attempts"],
                "seconds": measured["seconds"],
                "reasons": list(state.reasons),
            }
        )
    return rows


def freshness(context: Context, row: dict[str, Any] | None) -> str:
    """How much a stored verdict still says about the checkout, from files only."""
    from context_rail.gateway.handlers.checks import freshness_of

    return freshness_of(context, row)


def check_rows(context: Context, limit: int) -> tuple[list[dict[str, Any]], int]:
    """The Check states with their freshness, bounded, plus the omitted count."""
    rows = query.check_states(context.index)
    shown = [
        {
            "check": row["slug"],
            "item": row["item"],
            "passed": row["passed"],
            "freshness": freshness(context, row),
        }
        for row in rows[:limit]
    ]
    return shown, max(len(rows) - limit, 0)


def human_waits(context: Context, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Items and proposals waiting on a human, bounded, with the omitted count.

    Waits are queried on their own bound rather than filtered out of a page of
    newest reports, so a wait older than that page still reaches a human.
    """
    reports, reports_total = query.human_wait_reports(context.index, limit)
    proposals, proposals_total = query.human_proposals(context.index, limit)
    rows: list[dict[str, Any]] = [
        {
            "kind": "report",
            "item": report.get("item"),
            "report": report.get("report"),
            "reason": _narrative(report.get("data"), "what_happened")
            or report.get("target"),
            "at": report.get("at"),
        }
        for report in reports
    ]
    rows.extend(
        {
            "kind": "proposal",
            "item": proposal.get("slug"),
            "report": None,
            "reason": proposal.get("proposed_change"),
            "at": proposal.get("updated"),
        }
        for proposal in proposals
    )
    shown = rows[:limit]
    return shown, max(reports_total + proposals_total - len(shown), 0)


# ── the brief ─────────────────────────────────────────────────────────────


def brief(
    context: Context, *, max_chars: int = BRIEF_CHARS, item_limit: int = BRIEF_LIMIT
) -> dict[str, Any]:
    """The bounded CTO brief, its sections in the pinned priority order."""
    chars = _bounded(max_chars, BRIEF_CHARS_BOUNDS, "max_chars")
    limit = _bounded(item_limit, BRIEF_LIMIT_BOUNDS, "item_limit")
    index = context.index
    approval = approval_state(context)
    rows = query.brief_rows(index, limit)
    activity = query.digest_rows(index, None, limit)
    items = item_rows(index, context.pack)
    titles = item_titles(index)
    now_ms = int(time.time() * 1000)
    stuck = stuck_rows(items, now_ms, stuck_window_ms(context), titles)
    long_items = too_long_rows(context, items, titles)
    waits, waits_omitted = human_waits(context, limit)
    decisions, decisions_total = query.reversible_decisions(index, limit=limit)
    checks, checks_omitted = check_rows(context, limit)
    operating_state = _operating_state(context)
    headline = _headline(operating_state)
    sections, omitted = _brief_sections(
        context,
        approval=approval,
        counts=rows["counts"],
        reports=rows["reports"],
        activity=activity,
        items=items,
        titles=titles,
        stuck=stuck,
        too_long=long_items,
        waits=waits,
        waits_omitted=waits_omitted,
        decisions=decisions,
        decisions_omitted=decisions_total - len(decisions),
        checks=checks,
        checks_omitted=checks_omitted,
        limit=limit,
        operating_state=operating_state,
    )
    text, trimmed = _fit(sections, max(chars - len(headline) - 2, 1))
    for key, count in trimmed.items():
        omitted[key] += count
    text = headline + "\n\n" + text
    return {
        "text": text[:chars],
        "estimated_tokens": estimate_tokens(text[:chars]),
        "omitted": omitted,
        "approval": approval,
        "state": {
            "approval": approval["state"],
            "counts": rows["counts"],
            "stuck": len(stuck),
            "too_long": len(long_items),
            "needs_human": len(waits) + waits_omitted,
            **operating_state,
            "headline": headline,
        },
    }


def _operating_state(context: Context) -> dict[str, Any]:
    """The focus, unit and workflow progress one brief or digest reports."""
    from context_rail.context import operating

    model = getattr(context, "model", None)
    if model is None:
        return {"unit": None, "focus": {"state": "none", "focus": None}, "progress": None}
    unit = operating.resolve_unit(context, model)
    try:
        resolved = operating.resolve_focus(context, model)
        focus = resolved.as_dict()
    except operating.OperatingError as error:
        focus = {
            "state": "ambiguous" if error.code == "AMBIGUOUS_FOCUS" else "invalid",
            "focus": None,
            "candidates": error.details.get("candidates", []),
            "resolved_by": "refused",
            "error": error.code,
        }
    progress = operating.resolve_progress(context, model).as_dict()
    if unit is not None:
        progress["unit"] = unit.as_dict()
    return {
        "unit": None if unit is None else unit.as_dict(),
        "focus": focus,
        "progress": progress,
    }


def _headline(state: dict[str, Any]) -> str:
    progress = state.get("progress") or {}
    unit = state.get("unit") or {}
    noun = unit.get("label", "item")
    counted = f"{progress.get('complete', 0)}/{progress.get('total', 0)} {noun.lower()} done"
    focus = state.get("focus") or {}
    if focus.get("state") == "resolved" and focus.get("focus"):
        chosen = focus["focus"]
        return f"Focus: {chosen.get('title') or chosen.get('slug')} — {counted}"
    if focus.get("state") == "ambiguous":
        return f"Focus: ambiguous ({len(focus.get('candidates') or [])} candidates) — {counted}"
    return f"Focus: none (project-wide) — {counted}"


def _digest_focus(state: dict[str, Any]) -> str:
    focus = state.get("focus") or {}
    if focus.get("state") == "resolved" and focus.get("focus"):
        chosen = focus["focus"]
        return f"; focus {chosen.get('title') or chosen.get('slug')}"
    if focus.get("state") == "ambiguous":
        return "; focus ambiguous"
    return "; focus none"


def _workflow_lines(
    context: Context, items: Sequence[structure.ItemRow], limit: int
) -> tuple[list[str], int]:
    """Per-workflow counts plus the next action and proof for the top items."""
    from context_rail.context import surface

    open_items = [
        item
        for item in items
        if item.category not in structure.FINISHED_CATEGORIES
    ]
    groups: dict[str, dict[str, int]] = {}
    for item in open_items:
        counts = groups.setdefault(item.workflow, {})
        state = item.status or "unknown"
        counts[state] = counts.get(state, 0) + 1
    lines: list[str] = []
    for name in sorted(groups):
        counts = groups[name]
        summary = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
        lines.append(f"{name}: {sum(counts.values())} open ({summary})")
    shown = 0
    for item in sorted(open_items, key=lambda row: row.slug)[:limit]:
        try:
            view = surface.card(context, item.slug)
        except surface.SurfaceError:
            continue
        target = view["next"][0] if view["next"] else "none"
        proof = view["gate"]
        if view["proof"]["checks"]:
            proof += " (" + ", ".join(view["proof"]["checks"]) + ")"
        lines.append(
            f"- {item.slug} [{view['workflow']}/{view['state']}] "
            f"next {target}; proof {proof}"
        )
        shown += 1
    return lines, max(len(open_items) - shown, 0)


def _brief_sections(
    context: Context,
    *,
    approval: dict[str, Any],
    counts: dict[str, Any],
    reports: Sequence[dict[str, Any]],
    activity: dict[str, Any],
    items: Sequence[structure.ItemRow],
    titles: dict[str, str],
    stuck: Sequence[dict[str, Any]],
    too_long: Sequence[dict[str, Any]],
    waits: Sequence[dict[str, Any]],
    waits_omitted: int,
    decisions: Sequence[dict[str, Any]],
    decisions_omitted: int,
    checks: Sequence[dict[str, Any]],
    checks_omitted: int,
    limit: int,
    operating_state: dict[str, Any] | None = None,
) -> tuple[list[Section], dict[str, int]]:
    sections = [Section(key) for key in SECTION_KEYS]
    by_key = {section.key: section for section in sections}
    omitted = dict.fromkeys(SECTION_KEYS, 0)

    approval_section = by_key["approval"]
    approval_section.fixed.append(f"charter approval: {approval['state']}")
    if approval["state"] != "approved":
        approval_section.fixed.append(
            "blocked: nothing executes until a human approves the charter"
        )
    if approval.get("confirmer"):
        approval_section.fixed.append(
            f"approved by {approval['confirmer']} in {approval['latest_record']}"
        )
    approval_section.entries.extend(_wait_line(row) for row in waits)
    omitted["approval"] = waits_omitted

    goal_section = by_key["goal"]
    goal, scope, wont = picture(context.index)
    for title in goal[:limit]:
        goal_section.fixed.append(f"Goal: {title}")
    available = [f"{priority}: {title}" for priority, title in scope]
    available.extend(f"won't: {title}" for title in wont)
    goal_section.entries.extend(available[:limit])
    omitted["goal"] = max(len(available) - limit, 0) + max(len(goal) - limit, 0)

    charter_section = by_key["charter"]
    charter_section.fixed.extend(_charter_lines(context))
    charter_section.fixed.extend(_code_graph_lines(context))
    if context.policy is None:
        charter_section.entries.append("no charter document yet")

    workflow_section = by_key["workflows"]
    if operating_state and operating_state.get("unit"):
        workflow_section.fixed.append(
            f"unit: {operating_state['unit'].get('label')} "
            f"({operating_state['unit'].get('workflow')})"
        )
    workflow_lines, workflow_omitted = _workflow_lines(context, items, limit)
    workflow_section.entries.extend(workflow_lines)
    omitted["workflows"] = workflow_omitted

    report_section = by_key["reports"]
    report_counts = counts["reports"]
    report_section.fixed.append(
        f"untriaged: {report_counts['untriaged']} of {report_counts['total']}"
    )
    report_section.entries.extend(_report_line(row) for row in reports[:limit])
    omitted["reports"] = max(report_counts["untriaged"] - min(len(reports), limit), 0)

    executable_section = by_key["executable"]
    held = structure.blocked(list(items))
    runnable = structure.executable(list(items), held)
    finished = {
        item.slug
        for item in items
        if item.category in structure.FINISHED_CATEGORIES
    }
    held = {slug: reason for slug, reason in held.items() if slug not in finished}
    executable_section.fixed.append(f"executable: {len(runnable)} of {len(items)} item(s)")
    available = [f"run {slug} — {titles.get(slug, slug)}" for slug in runnable]
    available.extend(f"wait {slug} — blocked by {held[slug]}" for slug in sorted(held))
    executable_section.entries.extend(available[:limit])
    omitted["executable"] = max(len(available) - limit, 0)

    active_section = by_key["active"]
    categories = counts["items"]["by_category"]
    active_section.fixed.append(
        f"active: {categories.get('active', 0)} stuck: {len(stuck)} too long: {len(too_long)}"
    )
    available = [
        f"stuck {row['slug']} — quiet for {_hours(row['quiet_hours'])}" for row in stuck
    ]
    available.extend(
        f"too long {row['slug']} — {row['attempts']} attempt(s), {_seconds(row['seconds'])}"
        for row in too_long
    )
    available.extend(
        f"active {item.slug} — {titles.get(item.slug, item.slug)}"
        for item in items
        if item.category == "active"
    )
    active_section.entries.extend(available[:limit])
    omitted["active"] = max(len(available) - limit, 0)

    quality_section = by_key["quality"]
    quality_section.fixed.extend(_quality_lines(activity))
    rejected, rejected_total = query.rejected_reviews(context.index, limit=limit)
    available = [_run_line(row) for row in activity["runs"]]
    available.extend(
        f"review rejected {row['review']} — {row['item'] or '-'} (run {row['run'] or '-'})"
        for row in rejected
    )
    available.extend(
        f"check {row['check']} — {row['item'] or '-'} "
        f"{'passed' if row['passed'] else 'unproven'}, freshness {row['freshness']}"
        for row in checks
    )
    quality_section.entries.extend(available[:limit])
    omitted["quality"] = (
        max(len(available) - limit, 0)
        + checks_omitted
        + max(rejected_total - len(rejected), 0)
    )

    reversible_section = by_key["reversible"]
    if decisions:
        reversible_section.entries.extend(_decision_line(row) for row in decisions)
        omitted["reversible"] = decisions_omitted
    else:
        reversible_section.fixed.append("none")

    conventions_section = by_key["conventions"]
    memory, memory_omitted = _memory_rows(context.index, limit)
    conventions_section.entries.extend(memory)
    omitted["conventions"] = memory_omitted
    return sections, omitted


# ── the digest ────────────────────────────────────────────────────────────


def digest(
    context: Context,
    *,
    since: str | None = None,
    limit: int = BRIEF_LIMIT,
    max_chars: int = BRIEF_CHARS,
) -> dict[str, Any]:
    """The bounded digest: state counts, waits, decisions, stuck and quality."""
    chars = _bounded(max_chars, BRIEF_CHARS_BOUNDS, "max_chars")
    page = _bounded(limit, DIGEST_LIMIT_BOUNDS, "limit")
    index = context.index
    activity = query.digest_rows(index, since, page)
    approval = approval_state(context)
    items = item_rows(index, context.pack)
    titles = item_titles(index)
    now_ms = int(time.time() * 1000)
    stuck = stuck_rows(items, now_ms, stuck_window_ms(context), titles)
    long_items = too_long_rows(context, items, titles)
    waits, waits_omitted = human_waits(context, page)
    decisions, decisions_total = query.reversible_decisions(index, since, page)
    rejected, rejected_total = query.rejected_reviews(index, since, page)
    counts = activity["counts"]
    runnable = structure.executable(list(items))
    recent_reports = activity["reports"]
    operating_state = _operating_state(context)
    return {
        "summary": {
            "text": _clip(
                _summary_text(approval, counts, runnable, stuck, long_items)
                + _digest_focus(operating_state),
                chars,
            ),
            "counts": {
                "items": len(items),
                "executable": len(runnable),
                "stuck": len(stuck),
                "too_long": len(long_items),
                "runs": counts["runs"],
                "reports": counts["reports"],
                "untriaged_reports": counts["untriaged_reports"],
            },
            "approval": approval["state"],
            **operating_state,
        },
        "needs_human": list(waits),
        "reversible_decisions": list(decisions),
        "stuck": list(stuck[:page]),
        "too_long": list(long_items[:page]),
        "quality": {
            "checks": {
                "passed": activity["quality"]["verdicts"]["passed"],
                "failed": activity["quality"]["verdicts"]["failed"],
            },
            "reviews": {
                "approved": activity["quality"]["reviews"]["approve"],
                "rejected": activity["quality"]["reviews"]["reject"],
            },
            "runs": activity["quality"]["runs"],
            "rejected_reviews": list(rejected),
        },
        "usage": activity["usage"],
        "reports": {
            "total": counts["reports"],
            "untriaged": counts["untriaged_reports"],
            "triaged": counts["triaged"],
            "recent": list(recent_reports),
        },
        "omitted": {
            "needs_human": waits_omitted,
            "stuck": max(len(stuck) - page, 0),
            "too_long": max(len(long_items) - page, 0),
            "reversible_decisions": max(decisions_total - len(decisions), 0),
            "rejected_reviews": max(rejected_total - len(rejected), 0),
            "reports": max(counts["reports"] - len(recent_reports), 0),
        },
    }


def _summary_text(
    approval: dict[str, Any],
    counts: dict[str, Any],
    runnable: Sequence[str],
    stuck: Sequence[dict[str, Any]],
    too_long: Sequence[dict[str, Any]],
) -> str:
    return (
        f"approval {approval['state']}; {len(runnable)} executable, "
        f"{len(stuck)} stuck, {len(too_long)} too long, "
        f"{counts['untriaged_reports']} untriaged report(s) of {counts['reports']}, "
        f"{counts['runs']} run(s)"
    )


# ── selection helpers ─────────────────────────────────────────────────────


def picture(ix: Any) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """The picture's goal titles, scope items by priority and the Won't entries.

    A Won't note carries one title and a list of ``entries``; the entries are
    the decisions the human approved, so they are what the views show. Reading
    them from the indexed ``fields`` projection keeps this a pure index read.
    """
    rows = ix.conn.execute(
        "SELECT d.preset, d.title, p.value_json, e.value_json FROM docs d "
        "LEFT JOIN fields p ON p.path = d.path AND p.key = 'priority' "
        "LEFT JOIN fields e ON e.path = d.path AND e.key = 'entries' "
        "WHERE d.primitive IN ('item', 'note') AND d.broken = 0 "
        "AND d.preset IN ('goal', 'scope', 'wont') ORDER BY d.slug"
    ).fetchall()
    goal: list[str] = []
    wont: list[str] = []
    scope: list[tuple[str, str]] = []
    for preset, title, priority, entries in rows:
        name = title or ""
        if preset == "wont":
            wont.extend(_wont_entries(entries))
            continue
        if not name:
            continue
        if preset == "goal":
            goal.append(name)
        else:
            scope.append((priority or "could", name))
    order = {name: index for index, name in enumerate(_PRIORITIES)}
    scope.sort(key=lambda row: (order.get(row[0], len(order)), row[1]))
    return goal, scope, wont


def _wont_entries(raw: Any) -> list[str]:
    """The titles one Won't note lists, in the order it lists them."""
    if raw is None:
        return []
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    titles: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            titles.append(entry.strip())
        elif isinstance(entry, dict):
            title = entry.get("title")
            if isinstance(title, str) and title.strip():
                titles.append(title.strip())
    return titles


def approval_state(context: Context) -> dict[str, Any]:
    """The charter approval state plus who confirmed it, from the newest Record."""
    from context_rail.store.approval import approval_state as current_state

    state = current_state(context, CHARTER_TARGET)
    latest = query.approval_for(context.index, CHARTER_TARGET)
    return {
        **state,
        "confirmer": None if latest is None else latest["confirmer"],
        "at": None if latest is None else latest["at"],
    }


def _memory_rows(ix: Any, limit: int) -> tuple[list[str], int]:
    where = (
        "WHERE d.broken = 0 AND d.primitive = 'note' "
        "AND (d.preset = 'convention' OR d.path LIKE 'workspace/%')"
    )
    total = ix.conn.execute(f"SELECT COUNT(*) FROM docs d {where}").fetchone()[0]
    rows = ix.conn.execute(
        f"SELECT d.slug, d.title, d.excerpt FROM docs d {where} "
        "ORDER BY d.updated DESC, d.slug ASC LIMIT ?",
        (limit,),
    ).fetchall()
    shown = [
        f"{title or slug} — {_clip(excerpt or '', EXCERPT_CHARS)}"
        for slug, title, excerpt in rows
    ]
    return shown, max(total - len(rows), 0)


def _code_graph_lines(context: Context) -> list[str]:
    """One line when the code graph needs a human; none while it works or is off."""
    from pathlib import Path

    from context_rail.core.locate import StoreLocationError
    from context_rail.model.codegraph import effective_code_graph
    from context_rail.store.codegraph import last_event

    configured = effective_code_graph(
        context.manifest.code_graph, context.pack.policy.get("code_graph")
    )
    if configured == "none":
        return []
    store = Path(context.store)
    if configured != "builtin":
        from context_rail.runner.codegraph import readiness

        return [readiness(store, configured)["line"]]
    try:
        event = last_event(store)
    except StoreLocationError:
        return []
    if event is None or event.get("kind") not in _GRAPH_PROBLEMS:
        return []
    detail = event.get("detail") or {}
    reason = detail.get("message") or detail.get("reason") or ", ".join(detail.get("paths") or [])
    return [f"code graph: {_GRAPH_PROBLEMS[event['kind']]}: {reason}".rstrip(": ")]


_GRAPH_PROBLEMS = {
    "timeout": "timed out",
    "unavailable": "unavailable",
    "invalid": "cache invalid",
    "dirty_tree": "provider changed the working tree",
}


def _charter_lines(context: Context) -> list[str]:
    policy = context.policy
    if policy is None:
        return []
    gates = ", ".join(
        f"{name} {_gate_marks(gate)}" for name, gate in sorted(policy.gates.items())
    )
    rights = ", ".join(
        f"{name} {len(ids)}" for name, ids in sorted(policy.decision_rights.items())
    )
    return [
        f"gates: {gates}",
        f"rights: {rights}",
        f"budgets: item {_budget_text(policy.budgets.get('item'))}; "
        f"project {_budget_text(policy.budgets.get('project'))}",
        f"push: {'allowed' if policy.allow_push else 'never'}; separate reviewer "
        f"model: {'required' if policy.reviewer_different_model else 'optional'}",
    ]


def _quality_lines(activity: dict[str, Any]) -> list[str]:
    quality = activity["quality"]
    checks = quality["verdicts"]
    reviews = quality["reviews"]
    runs = quality["runs"]
    return [
        f"checks: {checks['passed']} passed, {checks['failed']} failed; "
        f"reviews: {reviews['approve']} approved, {reviews['reject']} rejected; "
        f"runs: {runs['accepted']} accepted, {runs['rejected']} rejected, "
        f"{runs['pending']} pending",
        f"usage: {_usage_text(activity['usage'])}",
    ]


def _fit(sections: list[Section], limit: int) -> tuple[str, dict[str, int]]:
    """Render the sections, trimming by the pinned priority until they fit.

    The order is explicit: the weakest excerpts go first, then the picture's
    goal lines, then the human waits, and the charter's facts and the approval
    blockers last. A section whose every line has gone is dropped rather than
    left as a bare heading, and both trimming and dropping count as omissions.
    """
    trimmed = {section.key: 0 for section in sections}
    length = len(_render(sections))
    for key, attribute in _TRIM_ORDER:
        if length <= limit:
            break
        section = _section(sections, key)
        if section is None:
            continue
        lines = getattr(section, attribute)
        while length > limit and lines:
            removed = lines.pop()
            trimmed[key] += 1
            prefix = 2 if attribute == "entries" else 0
            length -= len(removed) + prefix + 1
        if _is_empty(section):
            sections.remove(section)
            trimmed[key] += 1
            length = len(_render(sections))
    text = _render(sections)
    for key in _DROP_ORDER:
        if len(text) <= limit:
            break
        section = _section(sections, key)
        if section is None:
            continue
        sections.remove(section)
        trimmed[key] += max(1, len(section.fixed) + len(section.entries))
        text = _render(sections)
    return text[:limit], trimmed


def _section(sections: Sequence[Section], key: str) -> Section | None:
    return next((section for section in sections if section.key == key), None)


def _is_empty(section: Section) -> bool:
    return not section.fixed and not section.entries


#: The order a tight budget gives up lines in: weakest excerpt first, the human
#: waits once the picture's own lines are gone, the charter's facts after them.
_TRIM_ORDER = (
    ("conventions", "entries"),
    ("conventions", "fixed"),
    ("reversible", "entries"),
    ("reversible", "fixed"),
    ("quality", "entries"),
    ("quality", "fixed"),
    ("active", "entries"),
    ("active", "fixed"),
    ("workflows", "entries"),
    ("workflows", "fixed"),
    ("executable", "entries"),
    ("executable", "fixed"),
    ("reports", "entries"),
    ("reports", "fixed"),
    ("goal", "entries"),
    ("goal", "fixed"),
    ("approval", "entries"),
    ("charter", "entries"),
    ("charter", "fixed"),
)

#: Sections a budget may still drop whole, weakest first; approval is not one.
_DROP_ORDER = (
    "conventions",
    "reversible",
    "quality",
    "workflows",
    "active",
    "executable",
    "reports",
    "goal",
    "charter",
)


def _render(sections: Sequence[Section]) -> str:
    return "\n\n".join(section.render() for section in sections)


# ── rendering helpers ─────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """A labelled estimate: one token per four characters, never a real count."""
    return math.ceil(len(text) / 4)


def _wait_line(row: dict[str, Any]) -> str:
    reason = row.get("reason")
    if isinstance(reason, dict):
        reason = json.dumps(reason, sort_keys=True)
    return f"{row.get('kind')} {row.get('item') or '-'} — {reason or 'needs a human decision'}"


def _report_line(row: dict[str, Any]) -> str:
    state = "nothing to report" if row.get("nothing") else row.get("impact")
    return (
        f"[{state}] {row.get('target_class')} {row.get('target')} "
        f"— item {row.get('item') or '-'}"
    )


def _run_line(row: dict[str, Any]) -> str:
    accepted = {True: "accepted", False: "rejected", None: "pending"}[row.get("accepted")]
    usage = row.get("usage") or {}
    unknown = sum(1 for key in USAGE_KEYS if usage.get(key) == "unknown")
    return (
        f"run {row.get('run')} {row.get('role')} {row.get('item') or '(no item)'} "
        f"{row.get('status')} {accepted} {row.get('duration_s')}s "
        f"{row.get('attempt_count')} attempt(s), {unknown} unknown usage counter(s)"
    )


def _decision_line(row: dict[str, Any]) -> str:
    before = json.dumps(row.get("before"), sort_keys=True)
    after = json.dumps(row.get("after"), sort_keys=True)
    reason = row.get("reason") or "no reason given"
    return f"{row.get('decision')} {row.get('class')}: before {before} after {after} ({reason})"


def _gate_marks(gate: Any) -> str:
    marks = [
        name
        for name, needed in (
            ("checks", gate.require_checks),
            ("acceptance", gate.require_acceptance),
            ("review", gate.require_review),
            ("human", gate.require_human),
        )
        if needed
    ]
    return "(" + ", ".join(marks) + ")"


def _budget_text(caps: dict[str, Any] | None) -> str:
    if not caps:
        return "no limit"
    return ", ".join(
        f"{key} {caps.get(key) if caps.get(key) is not None else 'no limit'}"
        for key in ("attempts", "seconds", "tokens", "credits")
    )


def _usage_text(usage: dict[str, dict[str, Any]]) -> str:
    known = usage.get("known_sum") or {}
    unknown = usage.get("unknown_attempts") or {}
    parts: list[str] = []
    for key in USAGE_KEYS:
        if key in known:
            part = f"{key} {known[key]}"
            if unknown.get(key):
                part += f" (unknown {unknown[key]})"
            parts.append(part)
        elif unknown.get(key):
            parts.append(f"{key} unknown ({unknown[key]} attempt(s))")
    return ", ".join(parts) if parts else "unknown"


def _narrative(data: Any, key: str) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _hours(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g}h"


def _seconds(value: float) -> str:
    return f"{value:g}s"


def _iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def _bounded(value: Any, bounds: tuple[int, int], name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise query.QueryError(f"{name} must be an integer")
    low, high = bounds
    if value < low or value > high:
        raise query.QueryError(f"{name} must be between {low} and {high}")
    return value
