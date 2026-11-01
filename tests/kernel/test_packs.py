from __future__ import annotations

from context_rail.kernel.packs import parse_map_entries, plan_status_map
from context_rail.model.pack import load_pack
from helpers import init_store


def _packs(tmp_path):
    store = init_store(tmp_path)
    return {
        name: load_pack(name, store)
        for name in ("basic", "flow", "scrum", "waterfall")
    }


def test_same_name_and_unique_target_map_automatically(tmp_path) -> None:
    packs = _packs(tmp_path)
    used = frozenset({"backlog", "building", "blocked", "shipped", "dropped"})
    result = plan_status_map(packs["flow"], packs["basic"], used, {})
    assert result.problems == ()
    assert result.mapping == {
        "backlog": "todo",
        "building": "doing",
        "blocked": "blocked",
        "shipped": "done",
        "dropped": "dropped",
    }


def test_a_same_name_status_is_kept_even_when_the_category_has_others(tmp_path) -> None:
    packs = _packs(tmp_path)
    result = plan_status_map(
        packs["scrum"], packs["flow"], frozenset({"ready", "review"}), {}
    )
    assert result.problems == ()
    assert result.mapping == {"ready": "ready", "review": "review"}


def test_ambiguous_category_needs_an_explicit_map(tmp_path) -> None:
    packs = _packs(tmp_path)
    flow_to_scrum = plan_status_map(packs["flow"], packs["scrum"], frozenset({"building"}), {})
    assert flow_to_scrum.problems == (("ambiguous", "building"),)
    explicit = plan_status_map(
        packs["flow"], packs["scrum"], frozenset({"building"}), {"building": "doing"}
    )
    assert explicit.problems == () and explicit.mapping == {"building": "doing"}


def test_cross_category_and_unknown_maps_refuse(tmp_path) -> None:
    packs = _packs(tmp_path)
    cross = plan_status_map(
        packs["scrum"], packs["flow"], frozenset({"doing"}), {"doing": "shipped"}
    )
    assert ("cross_category", "doing") in cross.problems
    unknown_target = plan_status_map(
        packs["scrum"], packs["flow"], frozenset({"doing"}), {"doing": "nosuch"}
    )
    assert ("unknown_target", "doing") in unknown_target.problems
    unknown_source = plan_status_map(
        packs["scrum"], packs["flow"], frozenset({"doing"}), {"zzz": "building"}
    )
    assert ("unknown_source", "zzz") in unknown_source.problems


def test_duplicate_map_sources_are_reported() -> None:
    mapping, duplicates = parse_map_entries(["building=doing", "building=review"])
    assert duplicates == ("building",)
    assert mapping == {"building": "doing"}
