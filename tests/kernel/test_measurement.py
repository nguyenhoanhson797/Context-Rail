from __future__ import annotations

from fractions import Fraction

import pytest

from context_rail.kernel.measurement import (
    AttemptSample,
    compare_versions,
    measure_recurrence,
    summarize_attempts,
)


def _attempt(
    attempt_id: str,
    *,
    accepted: bool | None,
    attempt: int | None = 1,
    duration: float | None = 60.0,
    role: str | None = "implementer",
    model: str | None = "m1",
    version: int = 1,
    usage: dict | None = None,
) -> AttemptSample:
    return AttemptSample(
        attempt_id,
        role,
        model,
        accepted,
        {} if usage is None else usage,
        1,
        (("test-first", version),),
        attempt,
        duration,
        "t0",
    )


def _cohort(prefix: str, passes: int, fails: int, pending: int = 0, *, version: int = 1) -> list[AttemptSample]:
    items = [_attempt(f"{prefix}-p{i}", accepted=True, version=version) for i in range(passes)]
    items += [_attempt(f"{prefix}-f{i}", accepted=False, version=version) for i in range(fails)]
    items += [_attempt(f"{prefix}-n{i}", accepted=None, version=version) for i in range(pending)]
    return items


@pytest.mark.parametrize(("before", "after", "label"), [
    (_cohort("b", 5, 5), _cohort("a", 6, 4), "improved"),
    (_cohort("b", 5, 5), _cohort("a", 5, 5), "inconclusive"),
    (_cohort("b", 5, 5), _cohort("a", 4, 6), "regressed"),
])
def test_pass_rate_labels_at_the_threshold(before, after, label) -> None:
    result = compare_versions(summarize_attempts(before), summarize_attempts(after))
    assert result.pass_rate == label


def test_nine_comparable_candidates_is_insufficient_data() -> None:
    result = compare_versions(
        summarize_attempts(_cohort("b", 5, 4)), summarize_attempts(_cohort("a", 9, 1))
    )
    assert result.pass_rate == "insufficient-data"


def test_an_undecided_candidate_is_pending_and_not_in_the_pass_rate() -> None:
    summary = summarize_attempts(
        [_attempt("r1", accepted=True, attempt=2), _attempt("r2", accepted=None)]
    )
    assert summary.n == 2 and summary.pending == 1
    assert summary.pass_rate.numerator == 1 and summary.pass_rate.denominator == 1
    assert summary.attempts.mean == Fraction(3, 2)


def test_absent_counters_are_excluded_from_their_metric_only() -> None:
    summary = summarize_attempts(
        [_attempt("r1", accepted=True, duration=None), _attempt("r2", accepted=True, attempt=None)]
    )
    assert summary.n == 2
    assert summary.duration.known_n == 1 and summary.attempts.known_n == 1
    assert summary.pass_rate.numerator == 2 and summary.pass_rate.denominator == 2


def test_all_unknown_cost_is_unknown_not_zero() -> None:
    attempts = [
        _attempt(f"b{i}", accepted=True, usage={"factory_credits": None}) for i in range(10)
    ]
    summary = summarize_attempts(attempts)
    assert summary.usage["factory_credits"].coverage == "unknown"
    assert summary.usage["factory_credits"].total == "unknown"


def test_usage_keys_are_whatever_the_candidates_reported() -> None:
    summary = summarize_attempts(
        [
            _attempt("a", accepted=True, usage={"tool_calls": 3}),
            _attempt("b", accepted=True, usage={"tool_calls": 4}),
        ]
    )
    assert summary.usage["tool_calls"].total == 7
    assert summary.usage["tool_calls"].coverage == "complete"


def test_an_attempt_that_names_no_role_or_model_is_its_own_stratum() -> None:
    summary = summarize_attempts(
        [_attempt("a", accepted=True, role=None, model=None), _attempt("b", accepted=True)]
    )
    assert summary.strata == {("unknown", "unknown"): 1, ("implementer", "m1"): 1}


def test_mismatched_strata_are_inconclusive_with_counts() -> None:
    before = [_attempt(f"b{i}", accepted=True, model="m1") for i in range(10)]
    after = [_attempt(f"a{i}", accepted=True, model="m2") for i in range(10)]
    result = compare_versions(summarize_attempts(before), summarize_attempts(after))
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
