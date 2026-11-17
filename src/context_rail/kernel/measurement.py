"""Pure measurement: attempt summaries, version comparison and fix recurrence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction


IMPROVED = "improved"
REGRESSED = "regressed"
INCONCLUSIVE = "inconclusive"
INSUFFICIENT = "insufficient-data"
_THRESHOLD = Fraction(1, 10)


UNKNOWN_STRATUM = "unknown"

#: The identities a usage rollup may group by. Fixed, and refused when unknown.
GROUP_BY = ("item", "model", "harness", "role", "profile", "skill")


@dataclass(frozen=True)
class AttemptSample:
    """One recorded candidate, as far as measurement needs to know it.

    A missing role or model is its own stratum, ``"unknown"``, so attempts
    recorded by tools that name neither are only ever compared with each other.
    """

    attempt_id: str
    role: str | None
    model: str | None
    accepted: bool | None
    usage: Mapping[str, int | float | None]
    profile_version: int | None = None
    skills: tuple[tuple[str, int], ...] = ()
    attempt: int | None = None
    duration_s: float | None = None
    item: str | None = None
    harness: str | None = None
    profile: str | None = None

    @property
    def stratum(self) -> tuple[str, str]:
        return (self.role or UNKNOWN_STRATUM, self.model or UNKNOWN_STRATUM)


@dataclass(frozen=True)
class ReportSample:
    report_id: str
    at: str
    fingerprint: str


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def rate(self) -> Fraction | None:
        return Fraction(self.numerator, self.denominator) if self.denominator else None


@dataclass(frozen=True)
class Metric:
    mean: Fraction | None
    known_n: int


@dataclass(frozen=True)
class UsageTotal:
    total: int | float | str
    coverage: str
    known_n: int


@dataclass(frozen=True)
class AttemptSummary:
    n: int
    pending: int
    pass_rate: Rate
    attempts: Metric
    duration: Metric
    usage: dict[str, UsageTotal]
    strata: dict[tuple[str, str], int]


@dataclass(frozen=True)
class UsageGroup:
    """One identity's recorded effort, summed from the candidates that name it."""

    key: str
    summary: AttemptSummary


@dataclass(frozen=True)
class UsageRollup:
    """Bounded group rows, the whole-window total, and how many rows were trimmed."""

    group_by: str
    rows: tuple[UsageGroup, ...]
    total: AttemptSummary
    omitted: int


@dataclass(frozen=True)
class VersionComparison:
    pass_rate: str
    attempts: str
    duration: str
    quality: str
    usage: dict[str, str]
    reason: str | None
    strata: dict[str, dict[tuple[str, str], int]]
    before: AttemptSummary
    after: AttemptSummary


@dataclass(frozen=True)
class RecurrenceSummary:
    recurrence: str
    recurrence_count: int
    matched: tuple[str, ...]
    uncertainty: str | None


def summarize_attempts(attempts: Sequence[AttemptSample]) -> AttemptSummary:
    """Summarize recorded candidates; one without a verdict yet is pending."""
    decided = [sample for sample in attempts if sample.accepted is not None]
    numerator = sum(1 for sample in decided if sample.accepted is True)
    ordinals = [
        sample.attempt
        for sample in attempts
        if isinstance(sample.attempt, int) and not isinstance(sample.attempt, bool)
    ]
    durations = [
        sample.duration_s
        for sample in attempts
        if isinstance(sample.duration_s, (int, float))
        and not isinstance(sample.duration_s, bool)
        and sample.duration_s >= 0
    ]
    strata: dict[tuple[str, str], int] = {}
    for sample in attempts:
        strata[sample.stratum] = strata.get(sample.stratum, 0) + 1
    return AttemptSummary(
        n=len(attempts),
        pending=sum(1 for sample in attempts if sample.accepted is None),
        pass_rate=Rate(numerator=numerator, denominator=len(decided)),
        attempts=_metric(ordinals),
        duration=_metric(durations),
        usage=_usage_totals(attempts),
        strata=strata,
    )


def usage_rollup(
    samples: Sequence[AttemptSample], group_by: str, limit: int
) -> UsageRollup:
    """Group recorded effort by one identity, biggest group first.

    A candidate pinned to several skills belongs to each of those rows, so the
    rows are not additive. ``total`` is the only summary that counts every
    candidate exactly once, including the candidates whose row was trimmed.
    """
    if group_by not in GROUP_BY:
        raise ValueError(f"unknown group_by {group_by!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    buckets: dict[str, list[AttemptSample]] = {}
    for sample in samples:
        for key in _group_keys(sample, group_by):
            buckets.setdefault(key, []).append(sample)
    groups = [
        UsageGroup(key=key, summary=summarize_attempts(rows))
        for key, rows in buckets.items()
    ]
    groups.sort(key=lambda group: (-group.summary.n, group.key))
    return UsageRollup(
        group_by=group_by,
        rows=tuple(groups[:limit]),
        total=summarize_attempts(samples),
        omitted=max(0, len(groups) - limit),
    )


def compare_versions(
    before: AttemptSummary, after: AttemptSummary, *, min_runs: int = 10
) -> VersionComparison:
    """Label each metric with an explicit threshold, never guessing a value."""
    strata = {"before": dict(before.strata), "after": dict(after.strata)}
    keys = sorted({*before.usage, *after.usage})
    if set(before.strata) != set(after.strata):
        return VersionComparison(
            pass_rate=INCONCLUSIVE,
            attempts=INCONCLUSIVE,
            duration=INCONCLUSIVE,
            quality=INCONCLUSIVE,
            usage={key: INCONCLUSIVE for key in keys},
            reason="mismatched_strata",
            strata=strata,
            before=before,
            after=after,
        )
    if before.n < min_runs or after.n < min_runs:
        return VersionComparison(
            pass_rate=INSUFFICIENT,
            attempts=INSUFFICIENT,
            duration=INSUFFICIENT,
            quality=INSUFFICIENT,
            usage={key: INSUFFICIENT for key in keys},
            reason=INSUFFICIENT,
            strata=strata,
            before=before,
            after=after,
        )

    pass_rate = _rate_label(before.pass_rate, after.pass_rate)
    attempts_label = _metric_label(before.attempts, after.attempts, min_runs)
    duration_label = _metric_label(before.duration, after.duration, min_runs)
    unseen = UsageTotal(total="unknown", coverage="unknown", known_n=0)
    usage_labels = {
        key: _usage_label(
            before.usage.get(key, unseen), after.usage.get(key, unseen), min_runs
        )
        for key in keys
    }
    labels = (pass_rate, attempts_label, duration_label)
    if REGRESSED in labels:
        quality = REGRESSED
    elif INSUFFICIENT in labels:
        quality = INSUFFICIENT
    elif IMPROVED in labels:
        quality = IMPROVED
    else:
        quality = INCONCLUSIVE
    return VersionComparison(
        pass_rate=pass_rate,
        attempts=attempts_label,
        duration=duration_label,
        quality=quality,
        usage=usage_labels,
        reason=None,
        strata=strata,
        before=before,
        after=after,
    )


def measure_recurrence(
    reports: Sequence[ReportSample],
    *,
    cluster_keys: frozenset[str],
    boundary: tuple[str, str],
    eligible_runs: int | None,
    min_runs: int = 10,
) -> RecurrenceSummary:
    """Whether a matching report was filed strictly after the fix boundary."""
    matched = [
        report
        for report in reports
        if report.fingerprint in cluster_keys
        and (report.at, report.report_id) > boundary
    ]
    tied = any(
        report.fingerprint in cluster_keys and report.at == boundary[0]
        for report in reports
    )
    if matched:
        recurrence = "yes"
    elif eligible_runs is not None and eligible_runs >= min_runs:
        recurrence = "no-observed"
    else:
        recurrence = "unknown"
    return RecurrenceSummary(
        recurrence=recurrence,
        recurrence_count=len(matched),
        matched=tuple(sorted(report.report_id for report in matched)),
        uncertainty=(
            "reports and the boundary share a timestamp; the record id breaks the tie"
            if tied
            else None
        ),
    )


def _metric(values: Sequence[int | float]) -> Metric:
    if not values:
        return Metric(mean=None, known_n=0)
    return Metric(mean=sum(Fraction(value) for value in values) / len(values), known_n=len(values))


def _usage_totals(attempts: Sequence[AttemptSample]) -> dict[str, UsageTotal]:
    totals: dict[str, UsageTotal] = {}
    for key in sorted({name for sample in attempts for name in sample.usage}):
        values = [sample.usage.get(key) for sample in attempts]
        known = [
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not known:
            totals[key] = UsageTotal(total="unknown", coverage="unknown", known_n=0)
            continue
        coverage = "complete" if len(known) == len(attempts) else "partial"
        totals[key] = UsageTotal(total=sum(known), coverage=coverage, known_n=len(known))
    return totals


def _group_keys(sample: AttemptSample, group_by: str) -> tuple[str, ...]:
    if group_by == "skill":
        identities = tuple(sorted({identity for identity, _version in sample.skills}))
        return identities or (UNKNOWN_STRATUM,)
    value = {
        "item": sample.item,
        "model": sample.model,
        "harness": sample.harness,
        "role": sample.role,
        "profile": sample.profile,
    }[group_by]
    return (value or UNKNOWN_STRATUM,)


def _rate_label(before: Rate, after: Rate) -> str:
    first, second = before.rate, after.rate
    if first is None or second is None:
        return INSUFFICIENT
    return _direction(second - first, positive_is_regression=False)


def _metric_label(before: Metric, after: Metric, min_runs: int) -> str:
    if before.known_n < min_runs or after.known_n < min_runs:
        return INSUFFICIENT
    if before.mean is None or after.mean is None:
        return INSUFFICIENT
    if before.mean == 0 and after.mean == 0:
        return INCONCLUSIVE
    if before.mean == 0:
        return REGRESSED if after.mean > 0 else INCONCLUSIVE
    if after.mean == 0:
        return IMPROVED if before.mean > 0 else INCONCLUSIVE
    relative = (after.mean - before.mean) / before.mean
    return _direction(relative, positive_is_regression=True)


def _usage_label(before: UsageTotal, after: UsageTotal, min_runs: int) -> str:
    if before.known_n < min_runs or after.known_n < min_runs:
        return INSUFFICIENT
    if before.total == "unknown" or after.total == "unknown":
        return INSUFFICIENT
    first = Fraction(before.total)
    second = Fraction(after.total)
    if first == 0 and second == 0:
        return INCONCLUSIVE
    if first == 0:
        return REGRESSED if second > 0 else INCONCLUSIVE
    if second == 0:
        return IMPROVED if first > 0 else INCONCLUSIVE
    return _direction((second - first) / first, positive_is_regression=True)


def _direction(delta: Fraction, *, positive_is_regression: bool) -> str:
    if positive_is_regression:
        if delta <= -_THRESHOLD:
            return IMPROVED
        if delta >= _THRESHOLD:
            return REGRESSED
        return INCONCLUSIVE
    if delta >= _THRESHOLD:
        return IMPROVED
    if delta <= -_THRESHOLD:
        return REGRESSED
    return INCONCLUSIVE
