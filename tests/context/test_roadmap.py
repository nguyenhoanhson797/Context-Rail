"""The project map: spine, markers, traversal, search, change and budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from context_rail.context.operating import OperatingError
from context_rail.context.roadmap import resolve_map
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack


def _map(fx: SimpleNamespace, **kwargs: object) -> dict:
    ctx = Context(
        store=fx.store,
        manifest=load_manifest(fx.store),
        pack=load_pack(fx.pack, fx.store),
        actor="cto",
    )
    return resolve_map(ctx, fx.model, "cto", **kwargs)


def _add(fx: SimpleNamespace, preset: str, title: str, **extra: object) -> str:
    result = invoke(
        "add", {"preset": preset, "title": title, **extra}, store=fx.store, actor="cto"
    )
    assert is_ok(result), result
    value = result["value"]
    return value.get("slug") or value["doc"]["slug"]


def _nodes(view: dict) -> dict[str, dict]:
    return {node["slug"]: node for node in view["nodes"]}


def test_whole_map_places_items_on_the_spine_with_focus_next_and_rollup(
    operating_scrum_store: SimpleNamespace,
) -> None:
    fx = operating_scrum_store
    view = _map(fx)
    nodes = _nodes(view)

    assert nodes[fx.story]["parent"] == fx.sprint
    assert nodes[fx.task]["parent"] == fx.sprint
    assert nodes[fx.bug]["parent"] is None
    assert "focus" in nodes[fx.sprint]["markers"]
    assert "next" in nodes[fx.story]["markers"]
    assert nodes[fx.sprint]["rollup"] == {"complete": 0, "total": 1}
    assert view["where"]["focus"]["slug"] == fx.sprint
    assert view["text"].startswith("# Project map\nWhere we are: Sprint one")
    assert view["omitted"] == 0


def test_waterfall_roots_follow_the_hierarchy_order(
    operating_waterfall_store: SimpleNamespace,
) -> None:
    fx = operating_waterfall_store
    roots = [node["slug"] for node in _map(fx)["nodes"] if node["parent"] is None]

    assert roots[:2] == [fx.phase, fx.next_phase]


def test_centered_read_shows_path_siblings_attachments_and_dependencies(
    operating_scrum_store: SimpleNamespace,
) -> None:
    fx = operating_scrum_store
    note = _add(fx, "note", "Checkout copy tone", rel=[f"about={fx.story}"])
    decision = _add(fx, "decision", "Pay by card first", rel=[f"about={fx.story}"])
    _add(fx, "task", "Wire the button", rel=[f"part_of={fx.story}", f"depends_on={fx.bug}"])

    view = _map(fx, center=fx.story)

    assert [row["slug"] for row in view["breadcrumb"]] == [fx.sprint]
    assert [row["slug"] for row in view["siblings"]] == [fx.task]
    assert {row["slug"] for row in view["attachments"]} == {note, decision}
    assert view["center"]["attachments"] == 2
    child = next(node for node in view["nodes"] if node["parent"] == fx.story)
    assert "blocked" in child["markers"]
    assert "Path: Sprint one" in view["text"]
    assert "## Subtree" in view["text"]


def test_find_searches_titles_inside_one_branch(
    operating_scrum_store: SimpleNamespace,
) -> None:
    fx = operating_scrum_store
    everywhere = _map(fx, find="invoic")
    in_sprint = _map(fx, center=fx.sprint, find="copy")

    assert len(everywhere["matches"]) >= 2
    assert [row["slug"] for row in in_sprint["matches"]] == [fx.task]
    assert in_sprint["matches"][0]["path"] == "Sprint one"


def test_since_marks_only_what_moved_after_the_stamp(
    operating_scrum_store: SimpleNamespace,
) -> None:
    fx = operating_scrum_store
    everything = _map(fx, since="2000-01-01T00:00:00Z")
    nothing = _map(fx, since="2999-01-01T00:00:00Z")

    assert {row["slug"] for row in everything["changed"]} >= {fx.story, fx.sprint}
    assert nothing["changed"] == []


def test_depth_folds_branches_but_keeps_the_focus_open(
    operating_scrum_store: SimpleNamespace,
) -> None:
    fx = operating_scrum_store
    view = _map(fx, depth=0)
    shown = _nodes(view)

    assert fx.story in shown
    goal = next(node for node in view["nodes"] if node["children"] and node["slug"] != fx.sprint)
    assert f"{goal['slug']} " in view["text"] and "+3 below" in view["text"]
    assert view["omitted"] > 0


def test_budget_bounds_the_text_and_counts_what_it_hid(
    operating_scrum_store: SimpleNamespace,
) -> None:
    view = _map(operating_scrum_store, max_chars=400)

    assert len(view["text"]) <= 400
    assert view["omitted"] > 0


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"center": "no-such-item"}, "NOT_FOUND"),
        ({"depth": 13}, "VALIDATION_FAILED"),
        ({"depth": -1}, "VALIDATION_FAILED"),
        ({"max_chars": 50}, "VALIDATION_FAILED"),
        ({"find": "   "}, "VALIDATION_FAILED"),
        ({"since": "yesterday"}, "VALIDATION_FAILED"),
    ],
)
def test_bad_map_reads_are_refused(
    operating_scrum_store: SimpleNamespace, kwargs: dict, code: str
) -> None:
    with pytest.raises(OperatingError) as refused:
        _map(operating_scrum_store, **kwargs)
    assert refused.value.code == code


def test_a_note_is_not_a_map_center(operating_scrum_store: SimpleNamespace) -> None:
    fx = operating_scrum_store
    note = _add(fx, "note", "Loose thought")

    with pytest.raises(OperatingError) as refused:
        _map(fx, center=note)
    assert refused.value.code == "VALIDATION_FAILED"
