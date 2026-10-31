"""Read-only measurement handlers: version comparison and fix recurrence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from context_rail.core.util import canonical_iso
from context_rail.kernel.measurement import (
    RunSample,
    compare_versions,
    measure_recurrence,
    summarize_runs,
)
from context_rail.model.errors import ModelError
from context_rail.model.pack import effective_measurement_min_runs, load_pack
from context_rail.store import query
from context_rail.store.query import record_data
from context_rail.store.measure import reports_after_boundary, runs_for_versions
from context_rail.store.write import WriteRefused

if TYPE_CHECKING:  # imported lazily by the gateway only when a measure tool runs
    from context_rail.gateway.gateway import Context

KINDS = ("skill", "profile")
EVIDENCE_CAP = 50
LIMITATIONS = (
    "Measurement is observational: it reports indexed runs and reports, never "
    "causes or an automatic rollout. Zero observed recurrences is not proof of "
    "permanent resolution."
)


def measure_versions(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Compare two versions of one skill or profile from indexed run facts."""
    kind = args["target_class"]
    if kind not in KINDS:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "target_class must be skill or profile",
            {"target_class": kind},
        )
    target = args["target"]
    before_version = args["before"]
    after_version = args["after"]
    since, until = args["since"], args["until"]
    before, before_ex = runs_for_versions(
        ctx.index,
        kind=kind,
        target=target,
        versions=[before_version],
        since=since,
        until=until,
    )
    after, after_ex = runs_for_versions(
        ctx.index,
        kind=kind,
        target=target,
        versions=[after_version],
        since=since,
        until=until,
    )
    if kind == "skill":
        before, after, dropped = _skill_comparable(before, after, target)
    else:
        before, after, dropped = _profile_comparable(before, after)
    before_ex["non_comparable"] = before_ex.get("non_comparable", 0) + dropped["before"]
    after_ex["non_comparable"] = after_ex.get("non_comparable", 0) + dropped["after"]
    summary_before = summarize_runs(before)
    summary_after = summarize_runs(after)
    minimum = effective_measurement_min_runs(ctx.pack)
    comparison = compare_versions(summary_before, summary_after, min_runs=minimum)
    evidence = sorted({sample.run_id for sample in before + after})
    return {
        "label": comparison.quality,
        "summary": (
            f"quality {comparison.quality}; pass rate {comparison.pass_rate}; "
            f"attempts {comparison.attempts}; duration {comparison.duration}"
        ),
        "strata": comparison.strata,
        "exclusions": {"before": before_ex, "after": after_ex},
        "limitations": LIMITATIONS + f" At least {minimum} comparable runs are required per side.",
        "before": _side(before_version, summary_before, comparison.pass_rate),
        "after": _side(after_version, summary_after, comparison.pass_rate),
        "evidence": {"runs": evidence[:EVIDENCE_CAP], "omitted_count": max(0, len(evidence) - EVIDENCE_CAP)},
    }


def measure_fix(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Report whether a resolved fix's reports recurred after its boundary."""
    item = args["item"]
    until = args["until"]
    try:
        shown = query.show(ctx.index, item)
    except FileNotFoundError:
        raise LookupError("not_found") from None
    out = shown.get("out") or {}
    resolved = out.get("resolves") or []
    fingerprints = []
    for slug in resolved:
        record = _document_path(ctx, slug)
        if record is None:
            continue
        data = record_data(ctx.index, record)
        fingerprint = data.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            fingerprints.append(fingerprint)
    cluster_keys = frozenset(fingerprints)
    minimum = effective_measurement_min_runs(ctx.pack)
    boundary, reason = _fix_boundary(ctx, item)
    post_fix_runs = _runs_after(ctx, item, boundary, until) if boundary is not None else 0
    if boundary is None:
        report = measure_recurrence(
            [],
            cluster_keys=cluster_keys,
            boundary=("", ""),
            eligible_runs=None,
            min_runs=minimum,
        )
        recurrence, count = "inconclusive", 0
        matched: list[str] = []
    else:
        reports, omitted = reports_after_boundary(
            ctx.index,
            fingerprints=sorted(cluster_keys),
            boundary=boundary,
            until=until,
            limit=EVIDENCE_CAP,
        )
        report = measure_recurrence(
            reports,
            cluster_keys=cluster_keys,
            boundary=boundary,
            eligible_runs=post_fix_runs,
            min_runs=minimum,
        )
        recurrence, count = report.recurrence, report.recurrence_count
        matched = list(report.matched)
        if report.uncertainty:
            reason = reason or report.uncertainty
    if _category(ctx, item) != "done":
        recurrence, count = "inconclusive", count
        reason = "fix_not_currently_done"
    return {
        "boundary": boundary[1] if boundary is not None else None,
        "resolved_clusters": sorted(cluster_keys),
        "post_fix_runs": post_fix_runs,
        "recurrence": recurrence,
        "recurrence_count": count,
        "reason": reason,
        "evidence": {"reports": matched[:EVIDENCE_CAP], "omitted_count": max(0, count - EVIDENCE_CAP)},
        "limitations": LIMITATIONS,
    }


def _side(version: int, summary: Any, label: str) -> dict[str, Any]:
    return {
        "version": version,
        "runs": summary.n,
        "pending": summary.pending,
        "in_flight": summary.in_flight,
        "pass_rate": f"{summary.pass_rate.numerator}/{summary.pass_rate.denominator}",
        "attempts": _mean(summary.attempts.mean),
        "duration": _mean(summary.duration.mean),
        "label": label,
    }


def _mean(value: Any) -> str | None:
    return None if value is None else str(value)


def _signature(sample: RunSample, ignore: str) -> tuple[Any, ...]:
    others = tuple(sorted((identity, version) for identity, version in sample.skills if identity != ignore))
    return (sample.profile_version, others)


def _skill_comparable(
    before: list[RunSample], after: list[RunSample], target: str
) -> tuple[list[RunSample], list[RunSample], dict[str, int]]:
    def by_stratum(samples: list[RunSample]) -> dict[tuple[str, str], set[tuple[Any, ...]]]:
        groups: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
        for sample in samples:
            groups.setdefault((sample.role, sample.model), set()).add(
                _signature(sample, target)
            )
        return groups

    first = by_stratum(before)
    second = by_stratum(after)
    common: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
    for stratum, signatures in first.items():
        if stratum in second:
            shared = signatures & second[stratum]
            if shared:
                common[stratum] = shared
    kept_before = [sample for sample in before if _signature(sample, target) in common.get((sample.role, sample.model), set())]
    kept_after = [sample for sample in after if _signature(sample, target) in common.get((sample.role, sample.model), set())]
    return kept_before, kept_after, {
        "before": len(before) - len(kept_before),
        "after": len(after) - len(kept_after),
    }


def _profile_comparable(
    before: list[RunSample], after: list[RunSample]
) -> tuple[list[RunSample], list[RunSample], dict[str, int]]:
    def constant(samples: list[RunSample]) -> list[RunSample]:
        groups: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
        for sample in samples:
            signature = (sample.model, tuple(sorted(sample.skills)))
            groups.setdefault((sample.role, sample.model), set()).add(signature)
        keep: list[RunSample] = []
        for sample in samples:
            signatures = groups.get((sample.role, sample.model), set())
            signature = (sample.model, tuple(sorted(sample.skills)))
            if len(signatures) == 1:
                keep.append(sample)
        return keep

    kept_before = constant(before)
    kept_after = constant(after)
    return kept_before, kept_after, {
        "before": len(before) - len(kept_before),
        "after": len(after) - len(kept_after),
    }


def _document_path(ctx: Context, slug: str) -> str | None:
    row = ctx.index.conn.execute(
        "SELECT path FROM docs WHERE slug = ? ORDER BY path LIMIT 1", (slug,)
    ).fetchone()
    return row[0] if row is not None else None


def _category(ctx: Context, slug: str) -> str | None:
    row = ctx.index.conn.execute(
        "SELECT category FROM docs WHERE slug = ? ORDER BY path LIMIT 1", (slug,)
    ).fetchone()
    return row[0] if row is not None else None


def _fix_boundary(
    ctx: Context, item: str
) -> tuple[tuple[str, str] | None, str | None]:
    rows = ctx.index.conn.execute(
        "SELECT d.path, d.slug, f.value_json FROM docs d "
        "JOIN relations r ON r.src_path = d.path AND r.rel = 'about' AND r.dst = ? "
        "JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "LEFT JOIN fields f ON f.path = d.path AND f.key = 'at' "
        "WHERE k.value_json = ? ORDER BY d.record_at, d.slug",
        (item, json.dumps("status.changed")),
    ).fetchall()
    found: tuple[str, str] | None = None
    for path, slug, raw_at in rows:
        data = record_data(ctx.index, path)
        status = data.get("to")
        if not isinstance(status, str):
            continue
        at = _text_value(raw_at)
        if at is None:
            return None, "boundary_category_unknown"
        at = canonical_iso(at) or at
        category = _category_at(ctx, at, status)
        if category is None:
            return None, "boundary_category_unknown"
        if category == "done":
            found = (at, slug)
    return found, None


def _category_at(ctx: Context, at: str, status: str) -> str | None:
    pack_name = _pack_at(ctx, at)
    try:
        pack = load_pack(pack_name, ctx.store)
    except ModelError:
        return None
    return pack.statuses.get(status)


def _pack_at(ctx: Context, at: str) -> str:
    rows = ctx.index.conn.execute(
        "SELECT d.path, f.value_json FROM docs d "
        "JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "LEFT JOIN fields f ON f.path = d.path AND f.key = 'at' "
        "WHERE k.value_json = ? ORDER BY d.record_at, d.slug",
        (json.dumps("pack.changed"),),
    ).fetchall()
    changes: list[tuple[str, dict[str, Any]]] = []
    for path, raw_at in rows:
        change_at = _text_value(raw_at)
        if change_at is not None:
            changes.append(
                (canonical_iso(change_at) or change_at, record_data(ctx.index, path))
            )
    if not changes:
        return ctx.pack.name
    before = [change for change in changes if change[0] <= at]
    if before:
        name = before[-1][1].get("to")
        return name if isinstance(name, str) else ctx.pack.name
    first = changes[0][1].get("from")
    return first if isinstance(first, str) else ctx.pack.name


def _text_value(raw: Any) -> str | None:
    """The string a JSON-encoded field holds, or None when it holds something else."""
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, str) and value else None


def _runs_after(
    ctx: Context, item: str, boundary: tuple[str, str], until: str | None
) -> int:
    clause = "item = ? AND COALESCE(\"end\", start) > ?"
    params: list[Any] = [item, boundary[0]]
    if until is not None:
        clause += " AND COALESCE(\"end\", start) <= ?"
        params.append(until)
    row = ctx.index.conn.execute(
        f"SELECT count(*) FROM runs WHERE {clause} AND role = 'implementer'", params
    ).fetchone()
    return int(row[0]) if row is not None else 0

