"""Adapters sync: the managed AGENTS.md block and one SKILL.md per skill Note."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.adapters import END_MARK, START_MARK, payload_digest, read_managed
from context_rail.store.doc import etag
from helpers import latest_record

AGENTS = "AGENTS.md"
SKILL = ".agents/skills/test-first/SKILL.md"


def _agents(store: Path) -> Path:
    return store.parent / "AGENTS.md"


def _skill(store: Path) -> Path:
    return store.parent / ".agents" / "skills" / "test-first" / "SKILL.md"


def _records(store: Path) -> list[Path]:
    return sorted((store / "records").rglob("*.json"))


def test_first_sync_writes_and_second_is_a_no_op(skill_store: Path) -> None:
    first = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(first), first
    value = first["value"]
    assert sorted(value["written"]) == sorted([AGENTS, SKILL])
    assert value["dry_run"] is False and value["refused"] == []
    assert _agents(skill_store).is_file() and _skill(skill_store).is_file()
    assert value["record"] == latest_record(skill_store, "adapters.synced")["id"]

    second = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(second), second
    assert second["value"]["written"] == []
    assert sorted(second["value"]["unchanged"]) == sorted([AGENTS, SKILL])
    assert second["value"]["record"] is None


def test_dry_run_writes_nothing_and_names_every_target(skill_store: Path) -> None:
    before = _records(skill_store)
    result = invoke("adapters sync", {"dry_run": True}, store=skill_store, actor="cto")
    assert is_ok(result), result
    assert result["value"]["dry_run"] is True
    assert sorted(result["value"]["written"]) == sorted([AGENTS, SKILL])
    assert result["value"]["record"] is None
    assert not _agents(skill_store).exists()
    assert not (skill_store.parent / ".agents").exists()
    assert _records(skill_store) == before


def test_sync_writes_one_paired_block_with_a_digest_line(skill_store: Path) -> None:
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    text = _agents(skill_store).read_text(encoding="utf-8")
    assert text.count(START_MARK) == 1 and text.count(END_MARK) == 1
    assert text.index(START_MARK) < text.index(END_MARK)
    inner = text[text.index(START_MARK) : text.index(END_MARK)]
    assert "<!-- digest: sha256:" in inner
    payload, digest = read_managed(text)
    assert digest == payload_digest(payload)
    assert "test-first" in payload


def test_the_synced_record_names_outputs_digests_and_sources(skill_store: Path) -> None:
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    record = latest_record(skill_store, "adapters.synced")
    assert record["actor"] == "cto" and record["primitive"] == "record"
    data = record["data"]
    assert sorted(data["paths"]) == sorted([AGENTS, SKILL])
    assert sorted(data["payload_digests"]) == sorted([AGENTS, SKILL])
    assert data["harnesses"] == []
    assert list(data["source_etags"]) == ["workspace/skills/test-first.md"]

    agents_payload, _ = read_managed(_agents(skill_store).read_text(encoding="utf-8"))
    assert data["payload_digests"][AGENTS] == payload_digest(agents_payload)
    skill_payload, _ = read_managed(_skill(skill_store).read_text(encoding="utf-8"))
    assert data["payload_digests"][SKILL] == payload_digest(skill_payload)
    source = (skill_store / "workspace/skills/test-first.md").read_bytes()
    assert data["source_etags"]["workspace/skills/test-first.md"] == etag(source)


def test_a_skill_file_carries_valid_portable_frontmatter(skill_store: Path) -> None:
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    text = _skill(skill_store).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---\n")[1])
    assert frontmatter == {
        "name": "test-first",
        "description": "Write the failing test first",
    }
    body = text.split("---\n", 2)[2]
    assert body.index(START_MARK) < body.index(END_MARK)


def test_a_skill_body_without_a_trailing_newline_keeps_its_markers_apart(
    skill_store: Path,
) -> None:
    source = skill_store / "workspace/skills/test-first.md"
    source.write_text(
        source.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8", newline="\n"
    )
    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(result), result

    text = _skill(skill_store).read_text(encoding="utf-8")
    assert text.count(START_MARK) == 1 and text.count(END_MARK) == 1
    assert text.rstrip("\n").endswith(END_MARK)
    state = read_managed(text)
    assert state is not None
    payload, digest = state
    assert digest == payload_digest(payload)

    doctor = invoke("doctor", {}, store=skill_store, actor="cto")
    codes = [issue["code"] for issue in doctor["value"]["issues"]]
    assert "adapter_modified" not in codes


def test_human_prose_outside_the_markers_survives(skill_store: Path) -> None:
    agents = _agents(skill_store)
    prefix = "# Mine\n\nkeep me\n"
    agents.write_text(prefix, encoding="utf-8", newline="\n")
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    assert agents.read_text(encoding="utf-8").startswith(prefix)

    suffix = "\n## Tail\n\nkeep the tail\n"
    agents.write_text(agents.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    after = agents.read_text(encoding="utf-8")
    assert after.startswith(prefix) and after.endswith(suffix)


def test_a_source_change_regenerates_the_managed_payload(skill_store: Path, tmp_path: Path) -> None:
    agents = _agents(skill_store)
    agents.write_text("# Team notes\n\nkeep me\n", encoding="utf-8", newline="\n")
    invoke("adapters sync", {}, store=skill_store, actor="cto")

    body = tmp_path / "body.md"
    body.write_text("# test-first\n\nAlways prove it with a run.\n", encoding="utf-8")
    source = skill_store / "workspace/skills/test-first.md"
    updated = invoke(
        "update",
        {
            "slug": "test-first",
            "expect": etag(source.read_bytes()),
            "body_file": str(body),
            "key": "skill-body",
        },
        store=skill_store,
        actor="cto",
    )
    assert is_ok(updated), updated

    result = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(result), result
    assert sorted(result["value"]["written"]) == sorted([AGENTS, SKILL])
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# Team notes\n\nkeep me\n")
    assert "Always prove it with a run." in text
    assert "Always prove it with a run." in _skill(skill_store).read_text(encoding="utf-8")


def test_a_no_op_sync_appends_no_record(skill_store: Path) -> None:
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    count = len(_records(skill_store))
    invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert len(_records(skill_store)) == count
