from __future__ import annotations

from fractions import Fraction

import pytest

from context_rail.kernel.measurement import (
    RunSample,
    UsageSample,
    compare_versions,
    measure_recurrence,
    summarize_runs,
)

NO_USAGE = UsageSample(None, None, None, None, None)


def _run(run_id: str, *, accepted: bool | None, attempts: int | None = 1, duration: float | None = 60.0,
         in_flight: bool = False, role: str = "implementer", model: str = "m1", version: int = 1) -> RunSample:
    return RunSample(run_id, role, model, "implementer", 1, (("test-first", version),), "t0",
                     attempts, accepted, duration, NO_USAGE, not in_flight, in_flight)


def _cohort(prefix: str, passes: int, fails: int, pending: int = 0, *, version: int = 1) -> list[RunSample]:
    runs = [_run(f"{prefix}-p{i}", accepted=True, version=version) for i in range(passes)]
    runs += [_run(f"{prefix}-f{i}", accepted=False, version=version) for i in range(fails)]
    runs += [_run(f"{prefix}-n{i}", accepted=None, version=version) for i in range(pending)]
    return runs


@pytest.mark.parametrize(("before", "after", "label"), [
    (_cohort("b", 5, 5), _cohort("a", 6, 4), "improved"),
    (_cohort("b", 5, 5), _cohort("a", 5, 5), "inconclusive"),
    (_cohort("b", 5, 5), _cohort("a", 4, 6), "regressed"),
])
def test_pass_rate_labels_at_the_threshold(before, after, label) -> None:
    result = compare_versions(summarize_runs(before), summarize_runs(after))
    assert result.pass_rate == label


def test_nine_comparable_runs_is_insufficient_data() -> None:
    result = compare_versions(summarize_runs(_cohort("b", 5, 4)), summarize_runs(_cohort("a", 9, 1)))
    assert result.pass_rate == "insufficient-data"


def test_retries_are_one_sample_and_inflight_runs_are_excluded() -> None:
    runs = [_run("r1", accepted=True, attempts=2), _run("r2", accepted=None, in_flight=True)]
    summary = summarize_runs(runs)
    assert summary.n == 1 and summary.in_flight == 1
    assert summary.attempts.mean == Fraction(2, 1)


def test_malformed_and_absent_counters_are_excluded_from_their_metric_only() -> None:
    runs = [_run("r1", accepted=True, duration=None), _run("r2", accepted=True, attempts=None)]
    summary = summarize_runs(runs)
    assert summary.n == 2
    assert summary.duration.known_n == 1 and summary.attempts.known_n == 1
    assert summary.pass_rate.numerator == 2 and summary.pass_rate.denominator == 2


def test_reviewer_runs_never_enter_the_pass_rate() -> None:
    reviewer = _run("rv", accepted=True, role="reviewer")
    assert summarize_runs([reviewer]).pass_rate.denominator == 0


def test_all_unknown_cost_is_unknown_not_zero() -> None:
    summary = summarize_runs(_cohort("b", 10, 0))
    assert summary.usage["factory_credits"].coverage == "unknown"
    assert summary.usage["factory_credits"].total == "unknown"


def test_mismatched_strata_are_inconclusive_with_counts() -> None:
    before = [_run(f"b{i}", accepted=True, model="m1") for i in range(10)]
    after = [_run(f"a{i}", accepted=True, model="m2") for i in range(10)]
    result = compare_versions(summarize_runs(before), summarize_runs(after))
    assert result.pass_rate == "inconclusive"
    assert result.reason == "mismatched_strata" and result.strata["before"] == {("implementer", "m1"): 10}


def test_recurrence_labels() -> None:
    from context_rail.kernel.measurement import ReportSample

    boundary = ("2026-10-28T12:00:00Z", "01J0")
    one = ReportSample("01J1", "2026-10-28T13:00:00Z", "fp-1")
    assert measure_recurrence([one], cluster_keys=frozenset({"fp-1"}), boundary=boundary, eligible_runs=3).recurrence == "yes"
    assert measure_recurrence([], cluster_keys=frozenset({"fp-1"}), boundary=boundary, eligible_runs=10).recurrence == "no-observed"
    assert measure_recurrence([], cluster_keys=frozenset({"fp-1"}), boundary=boundary, eligible_runs=9).recurrence == "unknown"
    assert measure_recurrence([], cluster_keys=frozenset({"fp-1"}), boundary=boundary, eligible_runs=None).recurrence == "unknown"


def test_kernel_imports_no_io_module() -> None:
    import subprocess
    import sys

    code = (
        "import sys; import context_rail.kernel.measurement; "
        "bad = sorted(m for m in sys.modules if m.startswith(('context_rail.store', 'context_rail.runner', "
        "'context_rail.gateway', 'context_rail.store.index', 'sqlite3', 'subprocess'))); print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
