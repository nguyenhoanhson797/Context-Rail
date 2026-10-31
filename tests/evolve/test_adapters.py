from __future__ import annotations

from context_rail.store.measure import runs_for_versions
from helpers import write_versioned_run


def test_string_attempt_count_is_rejected_not_coerced(measure_store) -> None:
    write_versioned_run(measure_store.store, "bad", skills=(("test-first", 1),), attempts=2)
    path = next((measure_store.store / "records").rglob("*bad*.json"), None)
    assert path is not None
    samples, excluded = runs_for_versions(measure_store.ix, kind="skill", target="workspace/skills/test-first.md", versions=[1], since=None, until=None)
    assert samples[0].attempt_count == 2 and excluded.get("malformed", 0) == 0


def test_unattributed_runs_are_excluded_and_counted(measure_store) -> None:
    write_versioned_run(measure_store.store, "noskill", skills=(), attempts=1)
    samples, excluded = runs_for_versions(measure_store.ix, kind="skill", target="workspace/skills/test-first.md", versions=[1], since=None, until=None)
    assert samples == [] and excluded["unattributed"] == 1
