"""Pure measurement: run summaries, version comparison and fix recurrence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from context_rail.model.cto import USAGE_KEYS

IMPROVED = "improved"
REGRESSED = "regressed"
INCONCLUSIVE = "inconclusive"
INSUFFICIENT = "insufficient-data"
_THRESHOLD = Fraction(1, 10)


@dataclass(frozen=True)
class UsageSample:
    input_tokens: int | float | None
    output_tokens: int | float | None
    cache_read_input_tokens: int | float | None
    cache_creation_input_tokens: int | float | None
    factory_credits: int | float | None


@dataclass(frozen=True)
class RunSample:
    run_id: str
    role: str
    model: str
    profile: str
    profile_version: int
    skills: tuple[tuple[str, int], ...]
    item: str | None
    attempt_count: int | None
    accepted: bool | None
    duration_s: float | None
    usage: UsageSample
    completed: bool
    in_flight: bool


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
class RunSummary:
    n: int
    in_flight: int
    pending: int
    pass_rate: Rate
    attempts: Metric
    duration: Metric
    usage: dict[str, UsageTotal]
    strata: dict[tuple[str, str], int]


@dataclass(frozen=True)
class VersionComparison:
    pass_rate: str
    attempts: str
    duration: str
    quality: str
    usage: dict[str, str]
    reason: str | None
    strata: dict[str, dict[tuple[str, str], int]]
    before: RunSummary
    after: RunSummary


@dataclass(frozen=True)
class RecurrenceSummary:
    recurrence: str
    recurrence_count: int
    matched: tuple[str, ...]
    uncertainty: str | None


def summarize_runs(runs: Sequence[RunSample]) -> RunSummary:
    """Summarize implementer runs, excluding pending review and in-flight runs."""
    counted = [
        run
        for run in runs
        if run.role == "implementer" and run.completed and not run.in_flight
    ]
    in_flight = sum(
        1 for run in runs if run.role == "implementer" and run.in_flight
    )
    decided = [run for run in counted if run.accepted is not None]
    numerator = sum(1 for run in decided if run.accepted is True)
    attempts = [
        run.attempt_count
        for run in counted
        if isinstance(run.attempt_count, int) and not isinstance(run.attempt_count, bool)
    ]
    durations = [
        run.duration_s
        for run in counted
        if isinstance(run.duration_s, (int, float))
        and not isinstance(run.duration_s, bool)
        and run.duration_s >= 0
    ]
    strata: dict[tuple[str, str], int] = {}
    for run in counted:
        key = (run.role, run.model)
        strata[key] = strata.get(key, 0) + 1
    return RunSummary(
        n=len(counted),
        in_flight=in_flight,
        pending=sum(1 for run in counted if run.accepted is None),
        pass_rate=Rate(numerator=numerator, denominator=len(decided)),
        attempts=_metric(attempts),
        duration=_metric(durations),
        usage=_usage_totals(counted),
        strata=strata,
    )


def compare_versions(
    before: RunSummary, after: RunSummary, *, min_runs: int = 10
) -> VersionComparison:
    """Label each metric with an explicit threshold, never guessing a value."""
    strata = {"before": dict(before.strata), "after": dict(after.strata)}
    if set(before.strata) != set(after.strata):
        return VersionComparison(
            pass_rate=INCONCLUSIVE,
            attempts=INCONCLUSIVE,
            duration=INCONCLUSIVE,
            quality=INCONCLUSIVE,
            usage={key: INCONCLUSIVE for key in USAGE_KEYS},
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
            usage={key: INSUFFICIENT for key in USAGE_KEYS},
            reason=INSUFFICIENT,
            strata=strata,
            before=before,
            after=after,
        )

    pass_rate = _rate_label(before.pass_rate, after.pass_rate)
    attempts_label = _metric_label(before.attempts, after.attempts, min_runs)
    duration_label = _metric_label(before.duration, after.duration, min_runs)
    usage_labels = {
        key: _usage_label(before.usage[key], after.usage[key], min_runs)
        for key in USAGE_KEYS
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


def _usage_totals(runs: Sequence[RunSample]) -> dict[str, UsageTotal]:
    totals: dict[str, UsageTotal] = {}
    for key in USAGE_KEYS:
        values = [getattr(run.usage, key) for run in runs]
        known = [
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not runs or not known:
            totals[key] = UsageTotal(total="unknown", coverage="unknown", known_n=0)
            continue
        coverage = "complete" if len(known) == len(runs) else "partial"
        totals[key] = UsageTotal(total=sum(known), coverage=coverage, known_n=len(known))
    return totals


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
