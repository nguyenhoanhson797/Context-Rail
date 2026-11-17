from __future__ import annotations

from context_rail.gateway.gateway import invoke
from context_rail.store.measure import candidates_in_window
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


def test_candidates_in_window_selects_by_item_and_window(measure_store) -> None:
    store = measure_store.store
    write_attempt(store, "01JWNDCAND0000000000000001", item="t0", at="2026-10-01T00:00:00Z")
    write_attempt(store, "01JWNDCAND0000000000000002", item="t0", at="2026-10-15T00:00:00Z")
    write_attempt(store, "01JWNDCAND0000000000000003", item="t1", at="2026-10-15T00:00:00Z")
    ix = measure_store.ix
    samples, excluded = candidates_in_window(
        ix, item="t0", since="2026-10-10T00:00:00Z", until="2026-10-20T00:00:00Z"
    )
    assert [sample.attempt_id for sample in samples] == ["01JWNDCAND0000000000000002"]
    assert excluded == {"out_of_window": 0, "malformed": 0}
    assert [sample.item for sample in candidates_in_window(ix, item="t1")[0]] == ["t1"]
    assert len(candidates_in_window(ix)[0]) == 3


def test_window_samples_carry_the_identity_they_reported(measure_store) -> None:
    write_attempt(
        measure_store.store,
        "01JWNDCAND0000000000000001",
        model="m1",
        harness="opencode",
        profile="implementer",
        seat_role="cto",
        usage={"tokens": "unknown"},
    )
    samples, excluded = candidates_in_window(measure_store.ix)
    assert excluded == {"out_of_window": 0, "malformed": 0}
    sample = samples[0]
    assert (sample.model, sample.harness, sample.profile, sample.role) == (
        "m1",
        "opencode",
        "implementer",
        "cto",
    )
    assert sample.usage == {"tokens": None}


def test_a_window_with_no_candidates_is_empty_not_an_error(measure_store) -> None:
    samples, excluded = candidates_in_window(
        measure_store.ix, since="2030-01-01T00:00:00Z"
    )
    assert samples == []
    assert excluded == {"out_of_window": 0, "malformed": 0}
