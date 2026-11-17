from __future__ import annotations

from context_rail.gateway.gateway import invoke
from context_rail.store.measure import candidates_in_window, effort_samples
from helpers import record_result_for_test, write_attempt, write_review_verdict

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


def test_a_bound_result_supplies_only_the_effort_it_names(effort_store) -> None:
    store, task = effort_store.store, effort_store.task
    write_attempt(
        store, "cand-effort-1", item=task, commit="1" * 40, actor="implementer",
        model="m1", usage={"tokens": 10}, duration_s=60.0,
    )
    record_result_for_test(store, task, commit="1" * 40, effort={"usage": {"tokens": 99}})
    samples, _ = effort_samples(effort_store.ix)
    assert len(samples) == 1
    assert samples[0].usage == {"tokens": 99}
    assert samples[0].duration_s == 60.0
    assert samples[0].model == "m1"
    assert samples[0].attempt_id == "cand-effort-1"


def test_a_commitless_result_is_its_own_pending_attempt(effort_store) -> None:
    recorded = record_result_for_test(
        effort_store.store,
        effort_store.task,
        effort={"duration_s": 30, "usage": {"tokens": 5}},
        model="research-model",
        harness="opencode",
    )
    samples, _ = effort_samples(effort_store.ix)
    assert len(samples) == 1
    sample = samples[0]
    assert sample.attempt_id == recorded["record"]
    assert sample.accepted is None
    assert sample.duration_s == 30
    assert sample.usage == {"tokens": 5}
    assert (sample.model, sample.harness) == ("research-model", "opencode")


def test_two_results_on_one_commit_do_not_double_count(effort_store) -> None:
    store, task = effort_store.store, effort_store.task
    write_attempt(store, "cand-effort-1", item=task, commit="1" * 40, actor="implementer", usage={"tokens": 10})
    record_result_for_test(store, task, commit="1" * 40, effort={"usage": {"tokens": 20}})
    record_result_for_test(store, task, commit="1" * 40, effort={"usage": {"tokens": 30}})
    samples, _ = effort_samples(effort_store.ix)
    assert len(samples) == 1
    assert samples[0].usage == {"tokens": 30}


def test_two_candidates_on_one_commit_count_the_result_once(effort_store) -> None:
    store, task = effort_store.store, effort_store.task
    write_attempt(
        store, "cand-a-old", item=task, commit="2" * 40, actor="implementer", usage={"tokens": 10}
    )
    write_attempt(
        store, "cand-b-new", item=task, commit="2" * 40, actor="implementer", usage={"tokens": 20}
    )
    record_result_for_test(store, task, commit="2" * 40, effort={"usage": {"tokens": 99}})
    samples, _ = effort_samples(effort_store.ix)
    by_id = {sample.attempt_id: sample for sample in samples}
    assert len(samples) == 2
    assert by_id["cand-a-old"].usage == {"tokens": 10}
    assert by_id["cand-b-new"].usage == {"tokens": 99}


def test_a_result_outside_the_window_is_excluded(effort_store) -> None:
    store, task = effort_store.store, effort_store.task
    write_attempt(store, "cand-effort-1", item=task, commit="1" * 40, actor="implementer", at="2026-10-28T12:00:00Z")
    record_result_for_test(store, task, commit="1" * 40, effort={"usage": {"tokens": 20}})
    samples, excluded = effort_samples(
        effort_store.ix, since="2026-10-01T00:00:00Z", until="2026-10-31T00:00:00Z"
    )
    assert [sample.attempt_id for sample in samples] == ["cand-effort-1"]
    assert samples[0].usage == {}
    assert excluded == {"out_of_window": 0, "malformed": 0}
