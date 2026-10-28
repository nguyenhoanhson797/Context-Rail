"""rail.yaml validation and placement rendering (fail closed)."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from context_rail.model.manifest import (
    DEFAULT_MANIFEST,
    Manifest,
    ModelError,
    load_manifest,
    render_path,
)

PINNED_MANIFEST = {
    "schema": 3,
    "pack": "small",
    "placement": {
        "item": "items/{preset}/{slug}.md",
        "note": "notes/{preset}/{slug}.md",
        "check": "checks/{slug}.md",
        "record": "records/{yyyy}-{mm}/{id}.json",
        "actor": "workspace/profiles/{slug}/profile.md",
        "policy": "policies/{slug}.md",
    },
    "presets": {"decision": "records/decisions/{slug}.md"},
}


def _write_manifest(store: Path, payload: dict | str) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / "rail.yaml"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _manifest(**overrides: object) -> dict:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload.update(overrides)
    return payload


def _load_error(store: Path) -> ModelError:
    with pytest.raises(ModelError) as err:
        load_manifest(store)
    return err.value


def test_default_manifest_is_pinned() -> None:
    assert DEFAULT_MANIFEST == PINNED_MANIFEST


def test_render_default_paths(tmp_path: Path) -> None:
    _write_manifest(tmp_path, DEFAULT_MANIFEST)
    m = load_manifest(tmp_path)
    assert isinstance(m, Manifest)
    assert render_path(m, primitive="item", preset="task", slug="login") == (
        "items/task/login.md"
    )
    assert render_path(m, primitive="record", preset="decision", slug="use-sqlite") == (
        "records/decisions/use-sqlite.md"
    )
    assert render_path(
        m,
        primitive="record",
        preset="event",
        slug="x",
        record_id="01J",
        at="2026-10-28T00:00:00Z",
    ) == "records/2026-10/01J.json"


def test_loaded_manifest_exposes_schema_pack_and_placement(tmp_path: Path) -> None:
    _write_manifest(tmp_path, DEFAULT_MANIFEST)
    m = load_manifest(tmp_path)
    assert m.schema == 3
    assert m.pack == "small"
    assert m.placement == PINNED_MANIFEST["placement"]
    assert m.preset_placement == {"decision": "records/decisions/{slug}.md"}


def test_unknown_schema_fails_closed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "schema: 4\npack: small\nplacement: {}\n")
    assert _load_error(tmp_path).code == "SCHEMA_UNSUPPORTED"


def test_schema_is_checked_before_mandatory_fields(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "schema: 4\n")
    assert _load_error(tmp_path).code == "SCHEMA_UNSUPPORTED"


def test_unknown_manifest_key_fails_closed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _manifest(foo=1))
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_missing_primitive_placement_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    del payload["placement"]["actor"]
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_extra_placement_key_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["bogus"] = "x/{slug}.md"
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_unknown_placement_token_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["item"] = "items/{bogus}/{slug}.md"
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_unknown_preset_placement_token_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"]["decision"] = "records/decisions/{bogus}.md"
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_unmatched_brace_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["check"] = "checks/{slug.md"
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_missing_pack_key_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    del payload["pack"]
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_list_root_fails_closed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "- a\n- b\n")
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_non_string_placement_key_fails_closed(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "schema: 3\n"
        "pack: small\n"
        "placement:\n"
        '  item: "items/{preset}/{slug}.md"\n'
        '  note: "notes/{preset}/{slug}.md"\n'
        '  check: "checks/{slug}.md"\n'
        '  record: "records/{yyyy}-{mm}/{id}.json"\n'
        '  actor: "workspace/profiles/{slug}/profile.md"\n'
        '  policy: "policies/{slug}.md"\n'
        '  1: "x/{slug}.md"\n',
    )
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_preset_override_of_undeclared_preset_fails_closed(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"] = {"bogus": "x/{slug}.md"}
    _write_manifest(tmp_path, payload)
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_declared_preset_override_loads(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"]["task"] = "items/{preset}/{yyyy}/{slug}.md"
    _write_manifest(tmp_path, payload)
    m = load_manifest(tmp_path)
    assert render_path(
        m, primitive="item", preset="task", slug="login", at="2026-10-28T00:00:00Z"
    ) == "items/task/2026/login.md"


def test_preset_placement_wins_over_primitive_placement(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["presets"]["goal"] = "workspace/goals/{slug}.md"
    _write_manifest(tmp_path, payload)
    m = load_manifest(tmp_path)
    assert render_path(m, primitive="item", preset="goal", slug="auth") == (
        "workspace/goals/auth.md"
    )
    assert render_path(m, primitive="item", preset="task", slug="auth") == (
        "items/task/auth.md"
    )


def test_positive_control_multi_token_placement_loads(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    payload["placement"]["item"] = "items/{preset}/{yyyy}-{mm}/{slug}.md"
    _write_manifest(tmp_path, payload)
    m = load_manifest(tmp_path)
    assert render_path(
        m, primitive="item", preset="task", slug="login", at="2026-10-28T00:00:00Z"
    ) == "items/task/2026-10/login.md"


def test_manifest_without_presets_loads(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    del payload["presets"]
    _write_manifest(tmp_path, payload)
    assert load_manifest(tmp_path).preset_placement == {}


def test_missing_rail_yaml_fails_closed(tmp_path: Path) -> None:
    assert _load_error(tmp_path).code == "VALIDATION_FAILED"


def test_render_requires_the_tokens_the_template_uses(tmp_path: Path) -> None:
    _write_manifest(tmp_path, DEFAULT_MANIFEST)
    m = load_manifest(tmp_path)
    with pytest.raises(ModelError) as err:
        render_path(m, primitive="record", preset="event", slug="x")
    assert err.value.code == "VALIDATION_FAILED"


def test_render_refuses_an_unknown_primitive(tmp_path: Path) -> None:
    _write_manifest(tmp_path, DEFAULT_MANIFEST)
    m = load_manifest(tmp_path)
    with pytest.raises(ModelError) as err:
        render_path(m, primitive="widget", preset="task", slug="x")
    assert err.value.code == "VALIDATION_FAILED"


def test_manifest_cache_follows_path_mtime_and_size(tmp_path: Path) -> None:
    payload = copy.deepcopy(DEFAULT_MANIFEST)
    _write_manifest(tmp_path, payload)
    assert load_manifest(tmp_path).placement["note"] == "notes/{preset}/{slug}.md"

    payload["placement"]["note"] = "notex/{preset}/{slug}.md"
    path = _write_manifest(tmp_path, payload)
    stale = path.stat()
    os.utime(path, ns=(stale.st_atime_ns, stale.st_mtime_ns + 2_000_000_000))
    assert load_manifest(tmp_path).placement["note"] == "notex/{preset}/{slug}.md"
