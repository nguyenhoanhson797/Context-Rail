"""Queue resolution: presets, eligibility, focus priority and stable order."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.context.operating import OperatingError, resolve_queue
from context_rail.gateway.gateway import Context
from context_rail.model.manifest import load_manifest
from context_rail.model.operating import QueuePolicy
from context_rail.model.pack import load_pack
from helpers import init_store


def _context(store: Path, pack: str, actor: str = "cto") -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor=actor,
    )


def _slugs(view: object) -> list[str]:
    return [item.slug for item in view.items]


def test_the_scrum_queue_is_focus_first_and_stable(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")
    model = operating_scrum_store.model

    first = resolve_queue(ctx, model, "cto")
    second = resolve_queue(ctx, model, "cto")

    assert _slugs(first) == _slugs(second)
    assert set(_slugs(first)) == {
        operating_scrum_store.story,
        operating_scrum_store.task,
        operating_scrum_store.bug,
    }
    assert first.items[0].focus is True
    assert first.scope == "focus"
    assert first.focus.focus is not None
    assert first.focus.focus.slug == operating_scrum_store.sprint


def test_focus_only_filters_to_the_focus_descendants(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")

    view = resolve_queue(
        ctx, operating_scrum_store.model, "cto", focus_only=True
    )

    assert set(_slugs(view)) == {
        operating_scrum_store.story,
        operating_scrum_store.task,
    }


def test_all_items_bypasses_the_queue_presets(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")

    view = resolve_queue(
        ctx, operating_scrum_store.model, "cto", all=True
    )

    assert view.scope == "all"
    assert operating_scrum_store.sprint in _slugs(view)


def test_each_pack_returns_its_own_queue(
    operating_video_store: SimpleNamespace,
    operating_waterfall_store: SimpleNamespace,
) -> None:
    video = resolve_queue(
        _context(operating_video_store.store, "video"),
        operating_video_store.model,
        "cto",
    )
    waterfall = resolve_queue(
        _context(operating_waterfall_store.store, "waterfall"),
        operating_waterfall_store.model,
        "cto",
    )

    assert set(_slugs(video)) == {
        operating_video_store.shot,
        operating_video_store.second_shot,
    }
    assert set(_slugs(waterfall)) == {
        operating_waterfall_store.deliverable,
        operating_waterfall_store.other_deliverable,
    }


def test_require_action_omits_an_item_with_no_actor_action(
    operating_video_store: SimpleNamespace,
) -> None:
    model = operating_video_store.model
    policy = replace(
        model.operating.queue,
        presets=("shot", "asset"),
        require="action",
    )
    model = replace(model, operating=replace(model.operating, queue=policy))
    ctx = _context(operating_video_store.store, "video")

    view = resolve_queue(ctx, model, "cto")

    assert operating_video_store.shot in _slugs(view)
    assert operating_video_store.asset not in _slugs(view)
    assert view.omitted >= 1


def test_an_empty_project_has_an_empty_queue(tmp_path: Path) -> None:
    store = init_store(tmp_path / "empty", "scrum")
    ctx = _context(store, "scrum")
    from context_rail.model.compiler import load_project

    view = resolve_queue(ctx, load_project("scrum", store), "cto")

    assert view.items == ()
    assert view.scope == "project"
    assert view.focus.state == "none"


def test_an_ambiguous_focus_refuses_the_queue(
    operating_scrum_store: SimpleNamespace,
) -> None:
    from context_rail.core.result import is_ok
    from context_rail.gateway.gateway import invoke

    store = operating_scrum_store.store
    added = invoke(
        "add", {"preset": "sprint", "title": "Sprint two"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]
    etag = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]
    moved = invoke(
        "move", {"slug": slug, "status": "doing", "expect": etag}, store=store, actor="cto"
    )
    assert is_ok(moved), moved
    ctx = _context(store, "scrum")

    with pytest.raises(OperatingError) as caught:
        resolve_queue(ctx, operating_scrum_store.model, "cto")

    assert caught.value.code == "AMBIGUOUS_FOCUS"


def test_focus_only_without_a_focus_returns_nothing(
    operating_waterfall_store: SimpleNamespace,
) -> None:
    model = operating_waterfall_store.model
    focus = replace(model.operating.focus, resolve="none")
    model = replace(model, operating=replace(model.operating, focus=focus))
    ctx = _context(operating_waterfall_store.store, "waterfall")

    view = resolve_queue(ctx, model, "cto", focus_only=True)

    assert view.items == ()
    assert view.focus.state == "none"


def test_queue_order_is_total_across_mapping_order(
    operating_video_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_video_store.store, "video")

    first = resolve_queue(ctx, operating_video_store.model, "cto")
    reordered = replace(
        operating_video_store.model.operating,
        queue=QueuePolicy(
            presets=tuple(reversed(operating_video_store.model.operating.queue.presets))
        ),
    )
    second = resolve_queue(
        ctx,
        replace(operating_video_store.model, operating=reordered),
        "cto",
    )

    assert _slugs(first) == _slugs(second)
