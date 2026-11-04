"""Doctor findings for the operating model, and the severity map."""

from __future__ import annotations

from dataclasses import replace
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.doctor.checks import SEVERITIES, run_doctor
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from helpers import init_store, write_pack, write_record

OPERATING_CODES = {
    "focus_missing": "warning",
    "focus_ambiguous": "warning",
    "orphan_unit": "warning",
    "empty_container": "warning",
    "hierarchy_placement": "error",
    "surface_invalid": "warning",
    "progress_invalid": "error",
    "execution_failed": "warning",
    "execution_missing": "warning",
}


def _doctor(store: Path) -> dict:
    result = invoke("doctor", {}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def _pack_document(name: str) -> dict:
    text = resources.files("context_rail").joinpath(
        "packs", name, "pack.yaml"
    ).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _codes(store: Path) -> set[str]:
    return {issue["code"] for issue in _doctor(store)["issues"]}


def _add(store: Path, preset: str, title: str, **extra: object) -> str:
    result = invoke(
        "add", {"preset": preset, "title": title, **extra}, store=store, actor="cto"
    )
    assert is_ok(result), result
    return result["value"]["slug"]


def _move(store: Path, slug: str, status: str) -> None:
    etag = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]
    result = invoke(
        "move", {"slug": slug, "status": status, "expect": etag}, store=store, actor="cto"
    )
    assert is_ok(result), result


def test_a_clean_operating_store_has_no_operating_findings(
    operating_scrum_store: SimpleNamespace,
) -> None:
    assert not (_codes(operating_scrum_store.store) & set(OPERATING_CODES))


def test_focus_missing_is_a_warning(tmp_path: Path) -> None:
    store = init_store(tmp_path / "quiet", "scrum")
    _add(store, "sprint", "Not started")

    issues = [
        issue
        for issue in _doctor(store)["issues"]
        if issue["code"] == "focus_missing"
    ]

    assert issues
    assert issues[0]["severity"] == "warning"
    assert "rail next --all" in issues[0]["fix"]


def test_focus_ambiguous_names_the_candidates(
    operating_scrum_store: SimpleNamespace,
) -> None:
    second = _add(operating_scrum_store.store, "sprint", "Sprint two")
    _move(operating_scrum_store.store, second, "doing")

    issues = [
        issue
        for issue in _doctor(operating_scrum_store.store)["issues"]
        if issue["code"] == "focus_ambiguous"
    ]

    assert issues
    assert operating_scrum_store.sprint in issues[0]["message"]
    assert second in issues[0]["message"]


def test_an_orphan_unit_is_a_warning(
    operating_scrum_store: SimpleNamespace,
) -> None:
    orphan = _add(
        operating_scrum_store.store,
        "story",
        "Orphan",
        rel=[f"part_of={operating_scrum_store.scope}"],
    )

    issues = [
        issue
        for issue in _doctor(operating_scrum_store.store)["issues"]
        if issue["code"] == "orphan_unit"
    ]

    assert issues
    assert any(issue["path"].endswith(f"{orphan}.md") for issue in issues)


def test_an_empty_container_is_a_warning(
    operating_scrum_store: SimpleNamespace,
) -> None:
    empty = _add(operating_scrum_store.store, "sprint", "Empty sprint")
    _move(operating_scrum_store.store, empty, "doing")

    issues = [
        issue
        for issue in _doctor(operating_scrum_store.store)["issues"]
        if issue["code"] == "empty_container"
    ]

    assert issues
    assert any(f"{empty}.md" in issue["path"] for issue in issues)


def test_progress_invalid_is_reported_for_a_defensive_rule(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    pack = load_pack("scrum", store)
    operating = replace(
        pack.operating,
        progress=replace(pack.operating.progress, of=()),
    )
    broken = replace(pack, operating=operating)
    index = Index.open(store, broken)
    index.sync()
    try:
        result = run_doctor(store, load_manifest(store), broken, index)
    finally:
        index.close()

    codes = [issue["code"] for issue in result["issues"]]
    assert "progress_invalid" in codes


def test_a_bad_surface_is_a_warning(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    write_pack(store, "scrum", _pack_document("scrum"))
    surface = store / "packs" / "scrum" / "surface.yaml"
    surface.write_text("version: 2\n", encoding="utf-8", newline="\n")

    issues = [
        issue
        for issue in _doctor(store)["issues"]
        if issue["code"] == "surface_invalid"
    ]

    assert issues
    assert issues[0]["severity"] == "warning"


def test_a_hierarchy_preset_without_a_placement_is_an_error(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    pack = load_pack("scrum", store)
    manifest = load_manifest(store)
    stripped = replace(
        manifest,
        placement={
            key: value for key, value in manifest.placement.items() if key != "item"
        },
        preset_placement={},
    )
    index = Index.open(store, pack)
    index.sync()
    try:
        result = run_doctor(store, stripped, pack, index)
    finally:
        index.close()

    codes = [issue["code"] for issue in result["issues"]]
    assert "hierarchy_placement" in codes


def test_an_outermost_hierarchy_preset_needs_a_root_placement(
    operating_scrum_store: SimpleNamespace,
) -> None:
    store = operating_scrum_store.store
    pack = load_pack("scrum", store)
    manifest = load_manifest(store)
    broken = replace(
        manifest,
        preset_placement={"sprint": "{part_of}/{slug}.md"},
    )
    index = Index.open(store, pack)
    index.sync()
    try:
        result = run_doctor(store, broken, pack, index)
    finally:
        index.close()

    codes = [issue["code"] for issue in result["issues"]]
    assert "hierarchy_placement" in codes


def test_execution_missing_then_failed_then_succeeded(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = operating_video_store.shot
    for status in ("storyboard", "generating", "review", "approved"):
        _move(store, shot, status)

    missing = _codes(store)
    assert "execution_missing" in missing

    _write_execution(store, "01JDOCTOREXEC00000000001", shot, "failed", 1)
    failed = _codes(store)
    assert "execution_failed" in failed

    _write_execution(store, "01JDOCTOREXEC00000000002", shot, "succeeded", 2)
    clean = _codes(store)
    assert "execution_failed" not in clean
    assert "execution_missing" not in clean


def _write_execution(
    store: Path, record_id: str, target: str, state: str, attempt: int
) -> None:
    at = f"2026-11-04T10:0{attempt}:00Z"
    write_record(
        store,
        record_id,
        "execution.completed",
        {
            "schema": 1,
            "action": "render",
            "target": target,
            "state": state,
            "attempt": attempt,
            "started_at": at,
            "completed_at": at,
            "outputs": [] if state != "succeeded" else [f"renders/{target}.mp4"],
            "error": None if state != "failed" else "the encoder stopped",
            "actor": "renderer",
            "tool": "runner",
            "params": {},
            "runner_digest": "sha256:runner",
        },
        at=at,
        about=(target,),
    )


def test_an_invalid_pack_is_a_validation_refusal_not_a_doctor_cascade(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path / "bad", "scrum")
    document = {
        "name": "broken",
        "version": 2,
        "statuses": {"todo": "not_started", "done": "done", "dropped": "dropped"},
        "initial": "todo",
        "transitions": {"todo": ["done", "dropped"], "done": [], "dropped": []},
        "presets": {
            "sprint": {"primitive": "item", "tags": ["sprint"], "container": True}
        },
        "policy": {"stuck_hours": 24, "retries": 1},
        "operating": {
            "version": 1,
            "unit": "ghost",
            "hierarchy": [{"preset": "sprint"}],
        },
    }
    write_pack(store, "broken", document)

    validated = invoke("pack validate", {"name": "broken"}, store=store, actor="human")
    doctor = invoke("doctor", {}, store=store, actor="cto")

    assert validated["value"]["valid"] is False
    assert doctor["ok"] is True
    assert "progress_invalid" not in {issue["code"] for issue in doctor["value"]["issues"]}


def test_every_operating_code_is_classified() -> None:
    for code, severity in OPERATING_CODES.items():
        assert SEVERITIES[code] == severity
