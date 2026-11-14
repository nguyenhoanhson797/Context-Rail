"""Generated harness files point at the live briefs and drop what they no longer render."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.adapters import END_MARK
from context_rail.store.doc import etag, parse_doc
from helpers import latest_record

AGENTS = "AGENTS.md"
SKILL = ".agents/skills/test-first/SKILL.md"
BODY_LINE = "sentinel-7f3a: only the skill mirror carries this body line."


def _set_body(store: Path, tmp_path: Path, text: str) -> None:
    body = tmp_path / "body.md"
    body.write_text(f"# test-first\n\n{text}\n", encoding="utf-8")
    source = store / "workspace" / "skills" / "test-first.md"
    updated = invoke(
        "update",
        {"slug": "test-first", "expect": etag(source.read_bytes()), "body_file": str(body)},
        store=store,
        actor="cto",
    )
    assert is_ok(updated), updated


def _remove_note(store: Path) -> None:
    (store / "workspace" / "skills" / "test-first.md").unlink()
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))


def test_managed_block_indexes_skills_without_bodies(skill_store: Path, tmp_path: Path) -> None:
    _set_body(skill_store, tmp_path, BODY_LINE)
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(result), result
    agents = (skill_store.parent / AGENTS).read_text(encoding="utf-8")
    assert "test-first" in agents and "Write the failing test first" in agents
    assert BODY_LINE not in agents
    assert BODY_LINE in (skill_store.parent / SKILL).read_text(encoding="utf-8")


def test_managed_block_says_start_here(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    agents = (skill_store.parent / AGENTS).read_text(encoding="utf-8")
    assert "## Start here" in agents
    assert agents.index("## Start here") < agents.index("## The loop")
    assert "rail overview" in agents
    assert "rail loop brief <item> --role" in agents


def test_claude_agent_file_points_at_the_brief(approved_store: Path) -> None:
    result = invoke("adapters sync", {"harness": ["claude"]}, store=approved_store, actor="cto")
    assert is_ok(result), result
    agents_dir = approved_store.parent / ".claude" / "agents"
    implementer = (agents_dir / "implementer.md").read_text(encoding="utf-8")
    assert "rail loop brief <item> --role implementer" in implementer
    profile = parse_doc(
        "workspace/profiles/implementer/profile.md",
        (approved_store / "workspace" / "profiles" / "implementer" / "profile.md").read_bytes(),
    )
    first_rule = next(line for line in profile.body.splitlines() if line.startswith("- "))
    assert first_rule not in implementer
    assert "rail overview" in (agents_dir / "cto.md").read_text(encoding="utf-8")


def test_a_removed_skill_file_is_deleted_when_unedited(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    _remove_note(skill_store)
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(result), result
    assert result["value"]["written"] == [AGENTS]
    assert result["value"]["removed"] == [SKILL]
    assert not (skill_store.parent / SKILL).exists()
    data = latest_record(skill_store, "adapters.synced")["data"]
    assert SKILL not in data["paths"] and SKILL not in data["payload_digests"]
    assert "test-first" not in (skill_store.parent / AGENTS).read_text(encoding="utf-8")


def test_a_removed_skill_file_is_kept_when_edited(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    mirror = skill_store.parent / SKILL
    mirror.write_text(
        mirror.read_text(encoding="utf-8").replace(END_MARK, f"Hand note.\n{END_MARK}"),
        encoding="utf-8",
        newline="\n",
    )
    _remove_note(skill_store)
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert result["ok"] is False, result
    assert result["error"]["code"] == "CONFLICT"
    assert result["error"]["details"]["refused"] == [SKILL]
    assert result["error"]["details"]["reasons"][SKILL] == "orphan changed outside rail"
    assert "Hand note." in mirror.read_text(encoding="utf-8")


def test_a_harness_limited_sync_keeps_the_other_harness_files(skill_store: Path) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    result = invoke("adapters sync", {"harness": ["claude"]}, store=skill_store, actor="cto")
    assert is_ok(result), result
    assert result["value"]["removed"] == []
    assert (skill_store.parent / AGENTS).is_file()
    assert (skill_store.parent / SKILL).is_file()
