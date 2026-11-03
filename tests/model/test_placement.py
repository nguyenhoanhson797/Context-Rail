"""The placement grammar and manifest path rules (fail closed)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.model.errors import ModelError
from context_rail.model.manifest import (
    DEFAULT_MANIFEST,
    manifest_from_mapping,
    manifest_needs_graph,
    render_path,
)
from context_rail.model.placement import (
    check_placement_root,
    context_tokens,
    field_tokens,
    is_structural,
    parse_template,
    preset_tokens,
    render_simple,
    safe_relative_path,
    safe_segment,
    tokens_used,
)

PACK: dict[str, Any] = {
    "name": "basic",
    "version": 1,
    "statuses": {"todo": "not_started", "done": "done"},
    "initial": "todo",
    "transitions": {"todo": ["done"], "done": []},
    "presets": {
        "task": {"primitive": "item", "tags": ["task"]},
        "goal": {"primitive": "item", "tags": ["goal"]},
        "scope": {"primitive": "item", "tags": ["scope"]},
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": ["decision"]},
        "event": {"primitive": "record", "tags": ["event"]},
        "check": {"primitive": "check", "tags": []},
        "charter": {"primitive": "policy", "tags": []},
        "vision": {"primitive": "note", "tags": []},
        "wont": {"primitive": "note", "tags": []},
        "skill": {"primitive": "note", "tags": []},
        "cto": {"primitive": "actor", "tags": []},
        "profile": {"primitive": "actor", "tags": []},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
}


def _store(tmp_path: Path) -> Path:
    path = tmp_path / "packs" / "basic" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(PACK, sort_keys=False), encoding="utf-8")
    return tmp_path


def _manifest(store: Path, **presets: str) -> Any:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"].update(presets)
    return manifest_from_mapping(payload, store)


def _refused(store: Path, **presets: str) -> ModelError:
    with pytest.raises(ModelError) as error:
        _manifest(store, **presets)
    return error.value


@pytest.mark.parametrize(
    "template",
    [
        "{preset}",
        "{slug}",
        "{id}",
        "{kind}",
        "{yyyy}",
        "{yy}",
        "{mm}",
        "{dd}",
        "{part_of}",
        "{about}",
        "{verifies}",
        "{part_of.slug}",
        "{part_of.title}",
        "{about.slug}",
        "{about.title}",
        "{verifies.slug}",
        "{verifies.title}",
        "{item.slug}",
        "{item.title}",
        "{nearest:sprint}",
        "{nearest:sprint.slug}",
        "{nearest:sprint.title}",
        "{root:phase}",
        "{root:phase.slug}",
        "{root:phase.title}",
        "{field:area}",
    ],
)
def test_parses_every_token_form(template: str) -> None:
    tokens = parse_template(template, "placement")
    assert any(not token.literal for token in tokens)
    assert tokens_used(template) == tuple(token for token in tokens if not token.literal)


@pytest.mark.parametrize(
    "template",
    [
        "{bogus}",
        "{nearest:}",
        "{nearest:Sprint}",
        "{nearest:sprint.bad}",
        "{root:}",
        "{root:phase.bad}",
        "{field:}",
        "{field:a/b}",
        "{field:a b}",
        "{nearest:sn/print}",
        "{}",
        "{slug",
        "slug}",
        "{a{b}}",
    ],
)
def test_refuses_malformed_tokens(template: str) -> None:
    with pytest.raises(ModelError):
        parse_template(template, "placement")


def test_token_helpers() -> None:
    assert is_structural(parse_template("{part_of}", "x")[0])
    assert is_structural(parse_template("{kind}", "x")[0])
    assert not is_structural(parse_template("{slug}", "x")[0])
    assert context_tokens("{about}/{slug}.md")[0].kind == "about"
    assert field_tokens("{field:area}/{field:component}/{slug}.md") == (
        "area",
        "component",
    )
    assert preset_tokens("{nearest:sprint}/{root:phase}/{slug}.md") == (
        "sprint",
        "phase",
    )


def test_render_simple_requires_values() -> None:
    tokens = parse_template("{preset}/{slug}.md", "x")
    assert render_simple(tokens, {"preset": "task", "slug": "login"}) == "task/login.md"
    with pytest.raises(ModelError):
        render_simple(tokens, {"preset": "task"})


def test_render_simple_refuses_structural_tokens() -> None:
    with pytest.raises(ModelError) as error:
        render_simple(parse_template("{part_of}/{slug}.md", "x"), {})
    assert "placement context" in error.value.message


def test_safe_paths_accept_ordinary_segments() -> None:
    assert safe_relative_path("items/task/login.md") == "items/task/login.md"
    assert safe_segment("implement-checkout", "x") == "implement-checkout"


@pytest.mark.parametrize(
    "path",
    [
        "/absolute/x.md",
        "C:/x.md",
        "a\\b.md",
        "a/../b.md",
        "a/./b.md",
        "a//b.md",
        "a/",
        "CON/x.md",
        "a/nul.md",
        "a/" + "x" * 81 + ".md",
        "a/" + "x" * 200,
    ],
)
def test_safe_paths_refuse_unsafe_values(path: str) -> None:
    with pytest.raises(ModelError):
        safe_relative_path(path, "x")


@pytest.mark.parametrize("segment", ["", ".", "..", "a:b", "a?b", "a\x00b", "com1.txt"])
def test_safe_segment_refuses_unsafe_values(segment: str) -> None:
    with pytest.raises(ModelError):
        safe_segment(segment, "x")


def test_placement_root_rules() -> None:
    assert check_placement_root("items/x.md", "item") == "items/x.md"
    assert check_placement_root("records/x.json", "record") == "records/x.json"
    with pytest.raises(ModelError):
        check_placement_root("records/x.md", "item")
    with pytest.raises(ModelError):
        check_placement_root("items/x.json", "record")


def test_default_manifest_still_loads_and_renders(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = manifest_from_mapping(copy.deepcopy(DEFAULT_MANIFEST), store)
    assert render_path(manifest, primitive="item", preset="task", slug="login") == (
        "items/task/login.md"
    )
    assert render_path(manifest, primitive="record", preset="decision", slug="use-sqlite") == (
        "records/decisions/use-sqlite.md"
    )
    assert render_path(manifest, primitive="policy", preset="charter", slug="charter") == (
        "charter.md"
    )
    assert render_path(manifest, primitive="actor", preset="cto", slug="cto") == (
        "workspace/cto/profile.md"
    )


def test_duplicate_singletons_are_refused(tmp_path: Path) -> None:
    error = _refused(_store(tmp_path), wont="charter.md")
    assert "singleton" in error.message


def test_primitive_placement_needs_a_literal_first_segment(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["item"] = "{preset}/{slug}.md"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_primitive_placement_needs_an_identity_token(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["item"] = "items/fixed.md"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_preset_placement_may_start_with_a_path_token(tmp_path: Path) -> None:
    manifest = _manifest(_store(tmp_path), task="{part_of}/{slug}.md")
    assert manifest.preset_placement["task"] == "{part_of}/{slug}.md"


def test_preset_token_placement_needs_an_identity_token(tmp_path: Path) -> None:
    error = _refused(_store(tmp_path), task="{part_of}/fixed.md")
    assert "slug" in error.message or "id" in error.message


def test_preset_singleton_is_allowed(tmp_path: Path) -> None:
    manifest = _manifest(_store(tmp_path), charter="charter.md")
    assert manifest.preset_placement["charter"] == "charter.md"


def test_document_preset_may_not_enter_records(tmp_path: Path) -> None:
    _refused(_store(tmp_path), goal="records/goals/{slug}.md")


def test_record_preset_must_stay_under_records(tmp_path: Path) -> None:
    _refused(_store(tmp_path), decision="decisions/{slug}.md")


def test_fixed_skill_profile_and_actor_shapes(tmp_path: Path) -> None:
    _refused(_store(tmp_path), skill="knowledge/{slug}.md")
    _refused(_store(tmp_path), profile="workspace/people/{slug}.md")
    _refused(_store(tmp_path), cto="workspace/cto/me.md")


def test_fixed_actor_primitive_shape(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["actor"] = "workspace/people/{slug}.md"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_event_placement_must_be_context_free(tmp_path: Path) -> None:
    _refused(_store(tmp_path), event="records/{about}/{id}.json")


def test_record_primitive_context_needs_an_event_fallback(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["record"] = "records/{about}/{id}.json"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_record_primitive_context_with_a_fallback_is_allowed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["record"] = "records/{about}/{id}.json"
    payload["presets"]["event"] = "records/events/{id}.json"
    manifest = manifest_from_mapping(payload, store)
    assert manifest.placement["record"] == "records/{about}/{id}.json"


def test_kind_placement_is_allowed(tmp_path: Path) -> None:
    manifest = _manifest(
        _store(tmp_path), **{"kind:dispatch.started": "records/{about}/{id}.json"}
    )
    assert manifest.preset_placement["kind:dispatch.started"] == (
        "records/{about}/{id}.json"
    )


def test_kind_placement_may_use_item(tmp_path: Path) -> None:
    manifest = _manifest(
        _store(tmp_path), **{"kind:check.verdict": "records/{item.slug}/{id}.json"}
    )
    assert "kind:check.verdict" in manifest.preset_placement


@pytest.mark.parametrize(
    "template",
    [
        "records/{about}.json",
        "{about}/{id}.json",
        "records/fixed.json",
    ],
)
def test_kind_placement_needs_records_and_identity(tmp_path: Path, template: str) -> None:
    _refused(_store(tmp_path), **{"kind:dispatch.started": template})


def test_kind_placement_needs_a_kind(tmp_path: Path) -> None:
    _refused(_store(tmp_path), **{"kind:": "records/{id}.json"})


def test_item_tokens_are_kind_only(tmp_path: Path) -> None:
    _refused(_store(tmp_path), result="records/{item.slug}/{id}.json")
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["record"] = "records/{item.slug}/{id}.json"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_kind_token_is_record_only(tmp_path: Path) -> None:
    _refused(_store(tmp_path), goal="project/{kind}/{slug}.md")
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["item"] = "items/{kind}/{slug}.md"
    with pytest.raises(ModelError):
        manifest_from_mapping(payload, _store(tmp_path))


def test_preset_names_are_safe(tmp_path: Path) -> None:
    _refused(_store(tmp_path), **{"bad.name": "project/{slug}.md"})
    _refused(_store(tmp_path), **{"bad:name": "project/{slug}.md"})


def test_field_token_requires_required_or_defaulted_metadata(tmp_path: Path) -> None:
    _refused(_store(tmp_path), task="{part_of}/{field:area}/{slug}.md")


def test_manifest_needs_graph() -> None:
    simple = manifest_from_mapping(copy.deepcopy(DEFAULT_MANIFEST))
    assert manifest_needs_graph(simple) is False
    graph = copy.deepcopy(DEFAULT_MANIFEST)
    graph["presets"]["task"] = "{part_of}/{slug}.md"
    assert manifest_needs_graph(manifest_from_mapping(graph)) is True
    kind = copy.deepcopy(DEFAULT_MANIFEST)
    kind["presets"]["kind:dispatch.started"] = "records/{kind}/{id}.json"
    assert manifest_needs_graph(manifest_from_mapping(kind)) is True


def test_render_path_refuses_structural_templates(tmp_path: Path) -> None:
    manifest = _manifest(_store(tmp_path), task="{part_of}/{slug}.md")
    with pytest.raises(ModelError) as error:
        render_path(manifest, primitive="item", preset="task", slug="login")
    assert "placement context" in error.value.message


def test_render_path_renders_day_and_short_year(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["record"] = "records/{yyyy}-{yy}-{mm}-{dd}/{id}.json"
    manifest = manifest_from_mapping(payload, _store(tmp_path))
    assert render_path(
        manifest,
        primitive="record",
        preset="event",
        slug="x",
        record_id="01J",
        at="2026-10-28T23:30:00-02:00",
    ) == "records/2026-26-10-29/01J.json"
