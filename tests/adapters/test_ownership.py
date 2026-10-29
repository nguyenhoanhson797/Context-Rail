"""Ownership, force and confinement: a batch is refused whole, never half-applied."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import edited_block_before, junction

AGENTS = "AGENTS.md"
SKILL = ".agents/skills/test-first/SKILL.md"
SKILL_NAME = "../escape"

_ESCAPE_SKILL = f"""---
primitive: note
preset: skill
title: escape
tags: []
created: '2026-10-28T00:00:00Z'
updated: '2026-10-28T00:00:00Z'
version: 1
description: Try to leave the skills folder
schema: 1
name: '{SKILL_NAME}'
---
# escape
"""


def _agents(store: Path) -> Path:
    return store.parent / "AGENTS.md"


def _skill(store: Path) -> Path:
    return store.parent / ".agents" / "skills" / "test-first" / "SKILL.md"


def test_hand_edited_block_is_refused_without_force(skill_store: Path, edited_block: Path) -> None:
    before = edited_block_before(edited_block)
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert AGENTS in result["error"]["details"]["refused"]
    assert edited_block.read_text(encoding="utf-8") == before


def test_hand_edited_skill_file_is_refused_without_force(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    skill = _skill(skill_store)
    text = skill.read_text(encoding="utf-8")
    skill.write_text(
        text.replace("# test-first", "# test-first, edited by hand"), encoding="utf-8"
    )
    edited = skill.read_text(encoding="utf-8")

    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert SKILL in result["error"]["details"]["refused"]
    assert skill.read_text(encoding="utf-8") == edited


def test_doctor_reports_a_modified_adapter_as_a_warning(
    skill_store: Path, edited_block: Path
) -> None:
    doctor = invoke("doctor", {}, store=skill_store, actor="cto")
    assert is_ok(doctor), doctor
    modified = [
        issue for issue in doctor["value"]["issues"] if issue["code"] == "adapter_modified"
    ]
    assert [issue["path"] for issue in modified] == [AGENTS]
    assert modified[0]["severity"] == "warning"


def test_a_clean_sync_has_no_adapter_doctor_issues(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    doctor = invoke("doctor", {}, store=skill_store, actor="cto")
    codes = {issue["code"] for issue in doctor["value"]["issues"]}
    assert "adapter_modified" not in codes


def test_force_needs_human_and_the_exact_phrase(skill_store: Path, edited_block: Path) -> None:
    before = edited_block_before(edited_block)
    cto = invoke(
        "adapters sync",
        {"force": True, "confirm": "FORCE ADAPTERS adapters"},
        store=skill_store,
        actor="cto",
    )
    assert cto["error"]["code"] == "PERMISSION_DENIED", cto

    missing = invoke("adapters sync", {"force": True}, store=skill_store, actor="human")
    assert missing["error"]["code"] == "VALIDATION_FAILED", missing
    for phrase in ("FORCE ADAPTERS", "FORCE ADAPTERS adapters ", "force adapters adapters"):
        wrong = invoke(
            "adapters sync",
            {"force": True, "confirm": phrase},
            store=skill_store,
            actor="human",
        )
        assert wrong["error"]["code"] == "VALIDATION_FAILED", (phrase, wrong)
    assert edited_block.read_text(encoding="utf-8") == before


def test_force_rewrites_the_block_and_keeps_unmanaged_prose(skill_store: Path) -> None:
    agents = _agents(skill_store)
    prefix = "# Team notes\n\nkeep me\n"
    agents.write_text(prefix, encoding="utf-8", newline="\n")
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    text = agents.read_text(encoding="utf-8")
    agents.write_text(
        text.replace(
            "<!-- CONTEXT-RAIL-END -->",
            "hand edited line\n<!-- CONTEXT-RAIL-END -->",
        ),
        encoding="utf-8",
    )

    result = invoke(
        "adapters sync",
        {"force": True, "confirm": "FORCE ADAPTERS adapters", "key": "force-1"},
        store=skill_store,
        actor="human",
    )
    assert is_ok(result), result
    after = agents.read_text(encoding="utf-8")
    assert "hand edited line" not in after
    assert after.startswith(prefix)


def test_a_dry_run_names_conflicts_without_writing(skill_store: Path, edited_block: Path) -> None:
    before = edited_block_before(edited_block)
    skill_before = _skill(skill_store).read_bytes()
    records = sorted((skill_store / "records").rglob("*.json"))
    result = invoke("adapters sync", {"dry_run": True}, store=skill_store, actor="cto")
    assert is_ok(result), result
    assert result["value"]["refused"] == [AGENTS]
    assert result["value"]["written"] == []
    assert edited_block.read_text(encoding="utf-8") == before
    assert _skill(skill_store).read_bytes() == skill_before
    assert sorted((skill_store / "records").rglob("*.json")) == records


def test_a_refused_batch_names_the_reason(skill_store: Path, edited_block: Path) -> None:
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    reasons = result["error"]["details"]["reasons"]
    assert "AGENTS.md" in reasons
    assert "outside rail" in reasons["AGENTS.md"]


def test_malformed_markers_need_manual_repair_even_with_force(skill_store: Path) -> None:
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    agents = _agents(skill_store)
    agents.write_text(
        agents.read_text(encoding="utf-8") + "\n<!-- CONTEXT-RAIL-END -->\n",
        encoding="utf-8",
    )
    before = agents.read_text(encoding="utf-8")
    result = invoke(
        "adapters sync",
        {"force": True, "confirm": "FORCE ADAPTERS adapters"},
        store=skill_store,
        actor="human",
    )
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert "markers" in result["error"]["details"]["reasons"]["AGENTS.md"]
    assert agents.read_text(encoding="utf-8") == before


def test_an_unmarked_skill_file_is_never_replaced(skill_store: Path) -> None:
    unmarked = skill_store.parent / ".agents" / "skills" / "test-first" / "SKILL.md"
    unmarked.parent.mkdir(parents=True)
    unmarked.write_text("hand written\n", encoding="utf-8")
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert unmarked.read_text(encoding="utf-8") == "hand written\n"
    assert not _agents(skill_store).exists()


def test_a_junction_cannot_write_outside_the_repo(skill_store: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    junction(skill_store.parent / ".agents", outside)

    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "FORBIDDEN"
    assert list(outside.iterdir()) == []
    assert not _agents(skill_store).exists()


def test_two_sources_exporting_one_skill_name_are_refused(skill_store: Path) -> None:
    scoped = skill_store / "workspace/profiles/implementer/skills/test-first.md"
    scoped.parent.mkdir(parents=True)
    scoped.write_text(
        _ESCAPE_SKILL.replace(f"name: '{SKILL_NAME}'", "name: 'test-first'"),
        encoding="utf-8",
        newline="\n",
    )
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert not _agents(skill_store).exists()


def test_a_skill_name_cannot_escape_the_skills_folder(skill_store: Path) -> None:
    (skill_store / "workspace/skills/escape.md").write_text(
        _ESCAPE_SKILL, encoding="utf-8", newline="\n"
    )
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (skill_store.parent / "escape").exists()
    assert not (skill_store.parent.parent / "escape").exists()
    assert not _agents(skill_store).exists()
