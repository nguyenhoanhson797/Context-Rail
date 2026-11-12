from __future__ import annotations

from context_rail.store.measure import candidates_for_versions
from helpers import write_attempt

SKILL = "workspace/skills/test-first.md"


def test_a_pinned_candidate_is_attributed_to_its_skill_version(measure_store) -> None:
    write_attempt(measure_store.store, "bad", skills=(("test-first", 1),), attempt=2)
    measure_store.ix.sync()
    samples, excluded = candidates_for_versions(
        measure_store.ix, kind="skill", target=SKILL, versions=[1], since=None, until=None
    )
    assert samples[0].attempt == 2 and excluded.get("malformed", 0) == 0


def test_unattributed_candidates_are_excluded_and_counted(measure_store) -> None:
    write_attempt(measure_store.store, "noskill", skills=())
    measure_store.ix.sync()
    samples, excluded = candidates_for_versions(
        measure_store.ix, kind="skill", target=SKILL, versions=[1], since=None, until=None
    )
    assert samples == [] and excluded["unattributed"] == 1
