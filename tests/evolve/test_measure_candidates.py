from __future__ import annotations

from context_rail.gateway.gateway import invoke
from helpers import write_attempt, write_review_verdict

SKILL = "workspace/skills/test-first.md"


def _measure(store, **extra):
    args = {"target_class": "skill", "target": SKILL, "before": 1, "after": 2, **extra}
    result = invoke("measure versions", args, store=store, actor="cto")
    assert result["ok"] is True, result
    return result["value"]


def test_measure_versions_compares_candidates_by_their_review_outcome(measure_store) -> None:
    store = measure_store.store
    for number, (version, verdict) in enumerate(
        [(1, "reject"), (1, "reject"), (2, "approve"), (2, "approve"), (2, "reject")], start=1
    ):
        commit = str(number) * 40
        write_attempt(
            store,
            f"01JMEASCAND00000000000000{number}",
            commit=commit,
            skills=((SKILL, version),),
            usage={"input_tokens": 10 * number},
            at=f"2026-10-28T12:0{number}:00Z",
        )
        write_review_verdict(
            store,
            f"01JMEASREVIEW0000000000{number:03d}",
            "t0",
            commit,
            verdict=verdict,
            at=f"2026-10-28T13:0{number}:00Z",
        )
    value = _measure(store)
    assert value["before"]["candidates"] == 2
    assert value["after"]["candidates"] == 3
    assert value["before"]["pass_rate"] == "0/2"
    assert value["after"]["pass_rate"] == "2/3"


def test_a_candidate_without_a_verdict_is_pending_not_failed(measure_store) -> None:
    store = measure_store.store
    write_attempt(store, "01JMEASPEND0000000000000001", skills=((SKILL, 1),))
    write_attempt(store, "01JMEASPEND0000000000000002", skills=((SKILL, 2),))
    value = _measure(store)
    assert value["before"]["candidates"] == 1
    assert value["before"]["pending"] == 1
