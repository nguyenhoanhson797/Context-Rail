"""Reports and triage: one report per run, then fixes, proposals and exports.

A run owes exactly one structured report, and nothing-to-report is a report of
its own rather than an empty file. Triage reads only reports no triage Record
has claimed, groups them by the report's exact fingerprint, and acts once the
charter's thresholds are met: a fix Item the CTO may own, a proposal Note for a
human-only decision, or an ``engine-issues`` export kept inside the store.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso
from context_rail.gateway.handlers.common import (
    compose_doc,
    prepare_document_fields,
    preset_for,
    record_data,
    slug_of_path,
    unique_slug,
)
from context_rail.kernel.budgets import RunFacts
from context_rail.kernel.triage import (
    CTO_OWNED_CLASSES,
    ClusterDecision,
    ReportFacts,
    TriageThresholds,
    cluster_reports,
    report_fingerprint,
)
from context_rail.model.cto import REPORT_KEYS, Report, parse_report
from context_rail.model.manifest import render_path
from context_rail.model.policy import DEFAULT_THRESHOLDS
from context_rail.store import query
from context_rail.store.placement import (
    PlacementContext,
    PlacementError,
    PlacementRequest,
    expected_path,
)
from context_rail.store.approval import protected_paths
from context_rail.store.doc import etag
from context_rail.store.lock import store_lock
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

REPORT_KIND = "report.filed"
TRIAGE_KIND = "report.triaged"
ENGINE_DIR = "engine-issues"
EVIDENCE_CAP = 20
TEXT_MAX = 2000
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_HUMAN_TARGETS = frozenset({"charter", "goal", "vision", "wont"})
_RESERVED_NAME = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])", re.IGNORECASE)


def report(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """File the one report a run owes, or refuse the second one."""
    item = args.get("item")
    run = args["run"]
    payload = _load_report_file(args["report_file"])
    if item is not None:
        payload["item"] = item
    payload["run"] = run
    payload.setdefault("schema", 1)
    parsed = parse_report(payload)
    data = _report_data(parsed)
    if not data["fingerprint"]:
        data["fingerprint"] = report_fingerprint(
            data["target_class"], data["target"], data["expected"], data["observed"]
        )
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, args["key"])
        if saved is not None:
            return _report_value(index, saved)
        index.sync()
        existing = query.report_for_run(index, run)
        if existing is not None:
            raise WriteRefused(
                "CONFLICT",
                f"run {run!r} already has report {existing!r}",
                {"run": run, "report": existing},
            )
        linked = query.report_named_by_run(index, run)
        if linked is not None:
            raise WriteRefused(
                "CONFLICT",
                f"run {run!r} already links report {linked!r}",
                {"run": run, "report": linked},
            )
        result = commit(
            store,
            ctx.manifest,
            [],
            {
                "kind": REPORT_KIND,
                "about": [data["item"]] if data["item"] else [],
                "data": data,
            },
            actor=actor,
            key=args["key"],
            index=index,
        )
    return _report_value(index, result)


def triage(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Group untriaged reports and act on every cluster that earned it."""
    store = ctx.store
    key = args["key"]
    thresholds = _thresholds(ctx)
    value: dict[str, list[str]] = {
        "triaged": [],
        "deferred": [],
        "fix_items": [],
        "proposals": [],
        "engine_issues": [],
    }
    with store_lock(store):
        index = ctx.index
        index.sync()
        rows = query.untriaged_reports(index, args["limit"])
        if not rows:
            return value
        facts, members = _facts(rows)
        runs = tuple(
            RunFacts(
                item=None,
                role="implementer",
                attempts=0,
                seconds=0.0,
                usage=(),
                failed=bool(row["failed"]),
                run_id=row["run"],
            )
            for row in query.triage_runs(index, _run_ids(rows))
        )
        for decision in cluster_reports(facts, runs, thresholds):
            cluster_rows = members[decision.fingerprint]
            action = _routed_action(ctx, decision, cluster_rows)
            if action == "deferred":
                value["deferred"].extend(decision.reports)
                continue
            outputs = _act(ctx, decision, cluster_rows, action, actor, key)
            value["triaged"].extend(decision.reports)
            if action == "fix":
                value["fix_items"].extend(outputs)
            elif action == "proposal":
                value["proposals"].extend(outputs)
            elif action == "export":
                value["engine_issues"].extend(outputs)
        value["triaged"] = sorted(value["triaged"])
    return value


def _load_report_file(report_file: str) -> dict[str, Any]:
    path = Path(report_file)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND", f"report file {report_file!r} does not exist", {"path": report_file}
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"cannot read report file {report_file!r}: {exc}",
            {"path": report_file},
        ) from exc
    try:
        payload = json.loads(
            text, parse_constant=_refuse_constant, object_pairs_hook=_unique_keys
        )
    except _DuplicateKey as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"report file {report_file!r} repeats the key {exc.args[0]!r}",
            {"path": report_file},
        ) from None
    except ValueError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"report file {report_file!r} is not strict JSON: {exc}",
            {"path": report_file},
        ) from None
    if not isinstance(payload, dict):
        raise WriteRefused(
            "VALIDATION_FAILED",
            "a report file holds one JSON object",
            {"path": report_file},
        )
    return payload


def _refuse_constant(value: str) -> Any:
    raise ValueError(f"{value} is not a JSON number")


class _DuplicateKey(Exception):
    """A JSON object repeated a key; the report is refused instead of guessed."""


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name, value in pairs:
        if name in data:
            raise _DuplicateKey(name)
        data[name] = value
    return data


def _report_data(parsed: Report) -> dict[str, Any]:
    values = {
        "schema": parsed.schema,
        "target_class": parsed.target_class,
        "target": parsed.target,
        "item": parsed.item,
        "run": parsed.run,
        "nothing_to_report": parsed.nothing_to_report,
        "what_happened": parsed.what_happened,
        "expected": parsed.expected,
        "observed": parsed.observed,
        "impact": parsed.impact,
        "evidence": list(parsed.evidence),
        "fingerprint": parsed.fingerprint,
    }
    return {key: values[key] for key in REPORT_KEYS}


def _report_value(index: Any, result: dict[str, Any]) -> dict[str, Any]:
    record = result.get("record")
    data = record_data(index, record)
    return {
        "report": record,
        "nothing_to_report": bool(data.get("nothing_to_report")),
    }


def _thresholds(ctx: Context) -> TriageThresholds:
    policy = ctx.policy
    thresholds = DEFAULT_THRESHOLDS if policy is None else policy.thresholds
    return TriageThresholds(
        triage_reports=int(thresholds["triage_reports"]),
        triage_weight=int(thresholds["triage_weight"]),
    )


def _run_ids(rows: Sequence[dict[str, Any]]) -> list[str]:
    """The runs a page of reports is attributed to, ignoring an unattributed one."""
    return [run for run in (_attributed_run(row) for row in rows) if run is not None]


def _attributed_run(row: dict[str, Any]) -> str | None:
    """The run a report belongs to, by its own field or the run's report link.

    Dispatch files its report before the run exists, so a runless report is
    attributed through the completion payload that names it. Without that,
    every production report would vote alone and the failure evidence a run
    recorded could never double its weight.
    """
    run_id = row.get("run")
    if isinstance(run_id, str) and run_id:
        return run_id
    linked = row.get("linked_run")
    if isinstance(linked, str) and linked:
        return linked
    return None


def _vote_identity(row: dict[str, Any]) -> str:
    """The run a report votes for; a report no run names votes alone.

    A store can hold real reports that no run names; treating every one of them
    as one unknown run would merge unrelated reports into a single vote and no
    cluster could ever reach the threshold.
    """
    run_id = _attributed_run(row)
    if run_id is not None:
        return run_id
    return f"report:{row['record_id']}"


def _facts(
    rows: Sequence[dict[str, Any]],
) -> tuple[tuple[ReportFacts, ...], dict[str, tuple[dict[str, Any], ...]]]:
    facts: list[ReportFacts] = []
    members: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        fingerprint = _fingerprint(row)
        facts.append(
            ReportFacts(
                record_id=row["record_id"],
                run_id=_vote_identity(row),
                target_class=row["target_class"],
                target=row["target"],
                fingerprint=fingerprint,
                nothing_to_report=bool(row["nothing"]),
            )
        )
        members.setdefault(fingerprint, []).append(row)
    return tuple(facts), {key: tuple(value) for key, value in members.items()}


def _fingerprint(row: dict[str, Any]) -> str:
    stored = row.get("fingerprint")
    if isinstance(stored, str) and stored:
        return stored
    data = row.get("data") or {}
    return report_fingerprint(
        str(row["target_class"]),
        str(row["target"]),
        str(data.get("expected") or ""),
        str(data.get("observed") or ""),
    )


def _routed_action(
    ctx: Context, decision: ClusterDecision, rows: Sequence[dict[str, Any]]
) -> str:
    """The kernel's action, downgraded to a proposal when a human owns the target."""
    if decision.action != "fix":
        return decision.action
    return "proposal" if _human_only(ctx, rows) else "fix"


def _human_only(ctx: Context, rows: Sequence[dict[str, Any]]) -> bool:
    protected = set(protected_paths(ctx))
    for row in rows:
        if row["target_class"] not in CTO_OWNED_CLASSES:
            return True
        target = str(row["target"])
        if target in _HUMAN_TARGETS:
            return True
        paths = {
            found[0]
            for found in ctx.index.conn.execute(
                "SELECT path FROM docs WHERE slug = ?", (target,)
            )
        }
        if paths & protected:
            return True
    return False


def _act(
    ctx: Context,
    decision: ClusterDecision,
    rows: Sequence[dict[str, Any]],
    action: str,
    actor: str,
    key: str | None,
) -> list[str]:
    """Write one cluster's Record and its output in a single commit."""
    ops: list[Op] = []
    outputs: list[str] = []
    if action == "fix":
        rel, data = _fix_document(ctx, decision, rows)
        outputs.append(slug_of_path(rel))
        ops.append(Op(rel, data, ABSENT))
    elif action == "proposal":
        rel, data = _proposal_document(ctx, decision, rows)
        outputs.append(slug_of_path(rel))
        ops.append(Op(rel, data, ABSENT))
    elif action == "export":
        rel, data, expect = _engine_export(ctx, decision, rows)
        outputs.append(rel)
        ops.append(Op(rel, data, expect))
    record = {
        "kind": TRIAGE_KIND,
        "about": list(decision.reports),
        "data": {
            "schema": 1,
            "reports": list(decision.reports),
            "cluster": decision.fingerprint,
            "weight": decision.weight,
            "action": action,
            "outputs": list(outputs),
        },
    }
    commit(
        ctx.store,
        ctx.manifest,
        ops,
        record,
        actor=actor,
        key=_binding(key, decision),
        index=ctx.index,
    )
    return _existing_outputs(ctx, action, outputs)


def _binding(key: str | None, decision: ClusterDecision) -> str | None:
    """The idempotency binding for one cluster commit.

    The report set is part of the binding, so a reused key over new reports for
    the same fingerprint is a new piece of work rather than a replay of an
    older one. Only an identical request replays.
    """
    if key is None:
        return None
    reports = ",".join(decision.reports)
    return f"{key}:{decision.fingerprint}:{reports}"


def _existing_outputs(ctx: Context, action: str, outputs: Sequence[str]) -> list[str]:
    """Only the outputs that actually exist after the commit just applied."""
    if action == "export":
        return [path for path in outputs if (ctx.store / path).is_file()]
    found: list[str] = []
    for slug in outputs:
        row = ctx.index.conn.execute(
            "SELECT 1 FROM docs WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone()
        if row is not None:
            found.append(slug)
    return found


def _fix_document(
    ctx: Context, decision: ClusterDecision, rows: Sequence[dict[str, Any]]
) -> tuple[str, bytes]:
    preset = preset_for(ctx.pack, "fix")
    target = decision.fingerprint
    title = f"Fix {rows[0]['target']}"
    slug = unique_slug(ctx.index, title)
    stamp = now_iso()
    front: dict[str, Any] = {
        "primitive": "item",
        "preset": "fix",
        "title": title,
        "status": ctx.pack.initial,
        "tags": ["fix"],
        "created": stamp,
        "updated": stamp,
        "because_of": [f"[[{report}]]" for report in decision.reports],
        "resolves": [f"[[{report}]]" for report in decision.reports],
    }
    body = (
        f"# {title}\n\n"
        f"Triage cluster `{target}` weighed {decision.weight} across "
        f"{len(decision.runs)} run(s).\n\n"
        f"- expected: {rows[0]['data'].get('expected') or '(none)'}\n"
        f"- observed: {rows[0]['data'].get('observed') or '(none)'}\n"
    )
    front = prepare_document_fields(preset, front)
    try:
        rel = expected_path(
            PlacementContext(manifest=ctx.manifest, pack=ctx.pack, index=ctx.index),
            PlacementRequest(
                primitive="item",
                preset="fix",
                slug=slug,
                created=stamp,
                fields=front,
                relations={
                    name: tuple(front[name])
                    for name in ("because_of", "resolves")
                    if name in front
                },
            ),
        )
    except PlacementError as error:
        raise WriteRefused(error.write_code, error.message, error.details) from None
    return rel, compose_doc(front, body)


def _proposal_document(
    ctx: Context, decision: ClusterDecision, rows: Sequence[dict[str, Any]]
) -> tuple[str, bytes]:
    target = rows[0]["target"]
    title = f"Proposal: {target}"
    slug = unique_slug(ctx.index, title)
    change = _bounded(
        f"Change {target}: {rows[0]['data'].get('observed') or decision.reason}"
    )
    stamp = now_iso()
    payload = {
        "proposed_change": change,
        "decision_class": "human-only",
        "reports": list(decision.reports),
    }
    front: dict[str, Any] = {
        "primitive": "note",
        "preset": "note",
        "title": title,
        "tags": ["proposal"],
        "created": stamp,
        "updated": stamp,
        "proposed_change": change,
        "decision_class": "human-only",
        "data": payload,
        "because_of": [f"[[{report}]]" for report in decision.reports],
    }
    body = (
        f"# {title}\n\n"
        f"{change}\n\n"
        "This is not active policy. A human decision adopts it; the cluster's "
        f"reports are {', '.join(decision.reports)}.\n"
    )
    front = prepare_document_fields(ctx.pack.presets["note"], front)
    try:
        rel = expected_path(
            PlacementContext(manifest=ctx.manifest, pack=ctx.pack, index=ctx.index),
            PlacementRequest(
                primitive="note",
                preset="note",
                slug=slug,
                created=stamp,
                fields=front,
                relations={
                    name: tuple(front[name])
                    for name in ("because_of",)
                    if name in front
                },
            ),
        )
    except PlacementError as error:
        raise WriteRefused(error.write_code, error.message, error.details) from None
    return rel, compose_doc(front, body)


def _engine_export(
    ctx: Context, decision: ClusterDecision, rows: Sequence[dict[str, Any]]
) -> tuple[str, bytes, str]:
    """Rewrite one cluster's export from its whole bounded selection.

    A recurrence of the same fingerprint replaces the previous section instead
    of appending another, so the file always holds one heading and never more
    than the bounded evidence, no matter how often triage runs.
    """
    name = _cluster_name(decision.fingerprint)
    rel = f"{ENGINE_DIR}/{name}.md"
    history = _cluster_history(ctx, decision.fingerprint, rows)
    total = query.count_reports_by_fingerprint(ctx.index, decision.fingerprint)
    text = _export_section(decision, history, total=max(total, len(history)))
    path = ctx.store / rel
    if not path.is_file():
        return rel, text.encode("utf-8"), ABSENT
    return rel, text.encode("utf-8"), etag(path.read_bytes())


def _cluster_history(
    ctx: Context, fingerprint: str, rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every report ever filed under one fingerprint, newest first.

    The page being triaged supplies the new members; the whole-store selection
    supplies the members an earlier triage already claimed, so a recurrence
    keeps the evidence the first export held instead of dropping it.
    """
    seen = {row["record_id"]: row for row in rows}
    for row in query.reports_by_fingerprint(ctx.index, fingerprint, EVIDENCE_CAP * 2):
        seen.setdefault(row["record_id"], row)
    return sorted(
        seen.values(),
        key=lambda item: (str(item.get("at") or ""), str(item["record_id"])),
        reverse=True,
    )


def _cluster_name(fingerprint: str) -> str:
    cleaned = _UNSAFE_NAME.sub("-", fingerprint).strip("-._")[:80]
    if _RESERVED_NAME.fullmatch(cleaned.split(".")[0]):
        return "cluster-" + cleaned
    if cleaned:
        return cleaned
    return "cluster-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]


def _export_section(
    decision: ClusterDecision,
    rows: Sequence[dict[str, Any]],
    *,
    total: int | None = None,
) -> str:
    """One bounded export body: one heading, capped reports and evidence.

    ``total`` is how many reports the cluster has on file; the omission line is
    the difference against the reports this export could carry, so a capped
    read never claims it showed everything.
    """
    filed = len(rows) if total is None else max(total, len(rows))
    evidence: list[str] = []
    for row in rows:
        for entry in (row["data"].get("evidence") or [])[:EVIDENCE_CAP]:
            evidence.append(str(entry))
            if len(evidence) == EVIDENCE_CAP:
                break
        if len(evidence) == EVIDENCE_CAP:
            break
    lines = [
        f"# Engine issue: {decision.fingerprint}",
        "",
        f"- weight: {decision.weight} (this triage)",
        f"- reports on file: {filed}",
        f"- runs: {', '.join(decision.runs) or '(none)'}",
        f"- reports: {', '.join(decision.reports)}",
        "",
        "## Reports",
        "",
    ]
    for row in rows[:EVIDENCE_CAP]:
        data = row["data"]
        lines.append(
            f"- {row['record_id']} ({row['target_class']} {row['target']}): "
            f"{_bounded(str(data.get('what_happened') or '(nothing reported)'))}"
        )
    if filed > EVIDENCE_CAP:
        lines.append(f"- ({filed - EVIDENCE_CAP} more report(s) omitted)")
    lines.extend(["", "## Evidence", ""])
    lines.extend(f"- {entry}" for entry in evidence)
    if not evidence:
        lines.append("- (none filed)")
    return "\n".join(lines) + "\n"


def _bounded(text: str) -> str:
    if len(text) <= TEXT_MAX:
        return text
    return text[: TEXT_MAX - 1] + "…"
