"""Focus, unit and hierarchy resolution over the compiled operating model."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.context.operating import (
    OperatingError,
    resolve_focus,
    resolve_hierarchy,
    resolve_unit,
)
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import init_store


def _context(store: Path, pack: str) -> Context:
    return Context(
        store=store,
        manifest=load_manifest(store),
        pack=load_pack(pack, store),
        actor="cto",
    )


def _add(store: Path, preset: str, title: str, **extra: Any) -> str:
    result = invoke(
        "add", {"preset": preset, "title": title, **extra}, store=store, actor="cto"
    )
    assert is_ok(result), result
    return result["value"]["slug"]


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]


def _move(store: Path, slug: str, status: str) -> None:
    result = invoke(
        "move",
        {"slug": slug, "status": status, "expect": _etag(store, slug)},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result


def _with_first(model: Any, *, resolve: str) -> Any:
    focus = replace(model.operating.focus, resolve=resolve)
    return replace(model, operating=replace(model.operating, focus=focus))


def test_a_single_active_container_resolves_as_the_focus(
    operating_scrum_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_scrum_store.store, "scrum")

    resolved = resolve_focus(ctx, operating_scrum_store.model)

    assert resolved.state == "resolved"
    assert resolved.focus is not None
    assert resolved.focus.slug == operating_scrum_store.sprint
    assert resolved.resolved_by == "unique"


def test_no_active_container_is_a_none_state(tmp_path: Path) -> None:
    store = init_store(tmp_path / "quiet", "scrum")
    sprint = _add(store, "sprint", "Not started")
    context = _context(store, "scrum")
    model = load_pack("scrum", store)

    from context_rail.model.compiler import compile_project

    compiled = compile_project(model, model.operating)
    resolved = resolve_focus(context, compiled)

    assert resolved.state == "none"
    assert resolved.focus is None
    assert sprint


def test_two_active_containers_are_an_ambiguous_refusal(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    second = _add(store, "sprint", "Sprint two")
    _move(store, second, "doing")
    ctx = _context(store, "scrum")

    with pytest.raises(OperatingError) as caught:
        resolve_focus(ctx, operating_scrum_store.model)

    assert caught.value.code == "AMBIGUOUS_FOCUS"
    candidates = caught.value.details["candidates"]
    assert {row["slug"] for row in candidates} == {
        operating_scrum_store.sprint,
        second,
    }


def test_first_picks_the_ordered_container_and_records_it(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    earlier = _add(store, "scene", "Prologue", set=["sequence=0"])
    _move(store, earlier, "storyboard")
    model = _with_first(operating_video_store.model, resolve="first")
    ctx = _context(store, "video")

    resolved = resolve_focus(ctx, model)

    assert resolved.state == "resolved"
    assert resolved.focus is not None
    assert resolved.focus.slug == earlier
    assert resolved.resolved_by == "first"


def test_explicit_never_picks_implicitly(
    operating_video_store: SimpleNamespace,
) -> None:
    model = _with_first(operating_video_store.model, resolve="explicit")
    ctx = _context(operating_video_store.store, "video")

    implicit = resolve_focus(ctx, model)
    chosen = resolve_focus(ctx, model, into=operating_video_store.scene)

    assert implicit.state == "none"
    assert implicit.focus is None
    assert implicit.resolved_by == "explicit"
    assert chosen.state == "resolved"
    assert chosen.focus is not None
    assert chosen.focus.slug == operating_video_store.scene
    assert chosen.resolved_by == "into"


def test_none_never_resolves_a_focus(
    operating_video_store: SimpleNamespace,
) -> None:
    model = _with_first(operating_video_store.model, resolve="none")
    ctx = _context(operating_video_store.store, "video")

    resolved = resolve_focus(ctx, model)

    assert resolved.state == "none"
    assert resolved.focus is None


def test_into_refuses_a_wrong_preset_and_an_unrelated_item(
    operating_video_store: SimpleNamespace,
) -> None:
    ctx = _context(operating_video_store.store, "video")
    model = operating_video_store.model

    with pytest.raises(OperatingError) as wrong:
        resolve_focus(ctx, model, into=operating_video_store.shot)
    with pytest.raises(OperatingError) as missing:
        resolve_focus(ctx, model, into="nope")

    assert wrong.value.code == "INVALID_FOCUS"
    assert missing.value.code == "INVALID_FOCUS"


def test_a_focus_refusal_names_its_candidates(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    second = _add(store, "sprint", "Sprint two")
    _move(store, second, "doing")
    ctx = _context(store, "scrum")

    with pytest.raises(OperatingError) as caught:
        resolve_focus(ctx, operating_scrum_store.model)

    assert caught.value.details["preset"] == "sprint"
    assert caught.value.details["category"] == "active"
    assert len(caught.value.details["candidates"]) == 2


def test_resolving_is_read_only(
    operating_scrum_store: SimpleNamespace, tmp_path: Path
) -> None:
    store = operating_scrum_store.store
    before = {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file() and ".rail-local" not in path.parts
    }
    ctx = _context(store, "scrum")

    resolve_focus(ctx, operating_scrum_store.model)
    resolve_unit(ctx, operating_scrum_store.model)
    resolve_hierarchy(ctx, operating_scrum_store.model)

    after = {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file() and ".rail-local" not in path.parts
    }
    assert after == before


def test_the_unit_and_hierarchy_follow_the_pack(
    operating_scrum_store: SimpleNamespace,
    operating_waterfall_store: SimpleNamespace,
) -> None:
    scrum_ctx = _context(operating_scrum_store.store, "scrum")
    waterfall_ctx = _context(operating_waterfall_store.store, "waterfall")

    scrum_unit = resolve_unit(scrum_ctx, operating_scrum_store.model)
    waterfall_unit = resolve_unit(
        waterfall_ctx, operating_waterfall_store.model
    )
    scrum_groups = resolve_hierarchy(scrum_ctx, operating_scrum_store.model)
    waterfall_groups = resolve_hierarchy(
        waterfall_ctx, operating_waterfall_store.model
    )

    assert scrum_unit is not None and scrum_unit.preset == "story"
    assert scrum_unit.label == "Story"
    assert waterfall_unit is not None and waterfall_unit.preset == "deliverable"
    assert [group.preset for group in scrum_groups] == ["sprint"]
    assert scrum_groups[0].label == "Sprints"
    assert [group.preset for group in waterfall_groups] == ["phase"]
    assert [row.slug for row in waterfall_groups[0].items] == [
        operating_waterfall_store.phase,
        operating_waterfall_store.next_phase,
    ]
