"""Public documentation, CLI/MCP schema, and rendered-adapter tests for result records."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from helpers import as_human, init_store, mcp_list, run_cli

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.adapters import END_MARK, START_MARK

_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_PACKS = ("adaptive-contract", "basic", "flow", "scrum", "waterfall", "video", "portfolio")


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_cli_help_and_mcp_schemas_expose_result_and_review_contract_flags(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path / "proj", pack="basic")

    loop_help = run_cli(tmp_path, "loop", "--help")
    assert loop_help.returncode == 0, loop_help.stderr
    assert "result" in loop_help.stdout

    result_help = run_cli(tmp_path, "loop", "result", "--help")
    assert result_help.returncode == 0, result_help.stderr
    for flag in ("--body-file", "--commit", "--outcome", "--set", "--model", "--harness", "--key"):
        assert flag in result_help.stdout, flag
    for forbidden in ("--path", "--title"):
        assert forbidden not in result_help.stdout, forbidden

    review_help = run_cli(tmp_path, "loop", "review", "--help")
    assert review_help.returncode == 0, review_help.stderr
    for flag in ("--commit", "--result", "--verdict", "--notes-file", "--set"):
        assert flag in review_help.stdout, flag

    tools = {entry["name"]: entry for entry in mcp_list(store, actor="cto")}
    assert "result" in tools
    result_schema = tools["result"]["inputSchema"]
    assert set(result_schema["properties"]) == {
        "item",
        "body_file",
        "commit",
        "outcome",
        "set",
        "model",
        "harness",
        "key",
    }
    assert result_schema["additionalProperties"] is False
    assert set(result_schema["required"]) == {"item", "body_file"}

    review_schema = tools["review"]["inputSchema"]
    assert {"commit", "result", "notes_file", "set"} <= set(review_schema["properties"])
    assert "commit" not in review_schema["required"]
    assert "result" not in review_schema["required"]


@pytest.mark.parametrize("pack", _SHIPPED_PACKS)
def test_every_adapter_for_every_shipped_pack_teaches_result_protocol_under_50_lines(
    tmp_path: Path, pack: str
) -> None:
    store = init_store(tmp_path / pack, pack=pack)
    if pack == "portfolio":
        with as_human():
            synced = invoke(
                "adapters sync",
                {"harness": ["agents", "claude", "cursor", "gemini"]},
                store=store,
                actor=None,
            )
    else:
        synced = invoke(
            "adapters sync",
            {"harness": ["agents", "claude", "cursor", "gemini"]},
            store=store,
            actor="cto",
        )
    assert is_ok(synced), synced

    root = store.parent
    agents_text = (root / "AGENTS.md").read_text(encoding="utf-8")
    start_idx = agents_text.index(START_MARK)
    end_idx = agents_text.index(END_MARK, start_idx) + len(END_MARK)
    managed_block = agents_text[start_idx:end_idx]
    assert len(managed_block.splitlines()) < 50, (
        pack,
        len(managed_block.splitlines()),
        managed_block,
    )

    for rel in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursor/rules/context-rail.mdc"):
        body = (root / rel).read_text(encoding="utf-8")
        low = body.lower()
        assert "rail loop result" in low, (pack, rel)
        assert "discovery" in low or "commitless" in low, (pack, rel)
        assert "rail loop candidate" in low, (pack, rel)
        assert "cto" in low and ("markdown result" in low or "result record" in low), (pack, rel)


def test_customized_guidance_and_profiles_remain_protected_during_adoption(
    approved_store: Path,
) -> None:
    root = approved_store.parent
    assert is_ok(invoke("adapters sync", {}, store=approved_store, actor="cto"))

    agents_path = root / "AGENTS.md"
    agents_text = agents_path.read_text(encoding="utf-8")
    # Hand edit inside the managed block must refuse adapters sync without --force.
    edited_agents = agents_text.replace("## Start here", "## Custom Start", 1)
    agents_path.write_text(edited_agents, encoding="utf-8", newline="\n")
    refused = invoke("adapters sync", {}, store=approved_store, actor="cto")
    assert not is_ok(refused), refused
    assert refused["error"]["code"] == "CONFLICT"
    assert agents_path.read_text(encoding="utf-8") == edited_agents

    # Custom profile edit stays protected under upgrade.
    cto_profile = approved_store / "workspace/cto/profile.md"
    custom_cto = cto_profile.read_text(encoding="utf-8") + "\nCustom team note.\n"
    cto_profile.write_text(custom_cto, encoding="utf-8", newline="\n")
    upgraded = invoke(
        "upgrade", {"confirm": "UPGRADE store"}, store=approved_store, actor="human"
    )
    assert is_ok(upgraded), upgraded
    assert cto_profile.read_text(encoding="utf-8") == custom_cto
    customized_paths = {
        entry["path"] if isinstance(entry, dict) else entry
        for entry in upgraded["value"]["shipped"]["customized"]
    }
    assert "workspace/cto/profile.md" in customized_paths


def test_public_docs_and_skills_document_result_records_and_limits() -> None:
    expected_common = (
        "rail loop result",
        "256 KiB",
        "4000",
    )
    for rel in (
        "README.md",
        "docs/how-it-works.md",
        "docs/agent-onboarding/README.md",
        "docs/agent-onboarding/references/capabilities.md",
        "docs/agent-onboarding/references/commands.md",
        "skills/context-rail-loop/SKILL.md",
        "skills/context-rail/references/commands.md",
    ):
        text = _read(rel)
        assert "rail loop result" in text or "loop result" in text, rel

    loop_skill = _read("skills/context-rail-loop/SKILL.md")
    for phrase in (
        *expected_common,
        "262,144",
        "5",
        "20",
        "--key",
        "--commit",
        "--result",
        "ops upgrade",
        "can.result",
        "sees.parts",
        "Not applicable:",
        "unregistered",
        "duration_s",
        "usage: {tokens:",
        "unknown",
        "One loop for every work kind",
        "effort",
    ):
        assert phrase in loop_skill, phrase

    refusals_doc = _read("docs/agent-onboarding/references/refusals.md")
    assert "result" in refusals_doc.lower()

    prompts_doc = _read("docs/agent-onboarding/references/prompts.md")
    assert "rail loop result" in prompts_doc
    assert "notes-file" in prompts_doc

    # Public operator skills (except pack-authoring) use compiled reads, never pack.yaml.
    for path in sorted((_ROOT / "skills").rglob("SKILL.md")):
        if path.parent.name == "context-rail-pack-authoring":
            continue
        text = path.read_text(encoding="utf-8")
        assert "edit `pack.yaml`" not in text.lower(), path
        assert "parse `pack.yaml`" not in text.lower() or "never" in text.lower(), path

    # Pack-authoring alone explains declaring results contracts in pack.yaml.
    authoring = _read("skills/context-rail-pack-authoring/SKILL.md")
    assert "results:" in authoring and "produce:" in authoring and "review:" in authoring
    assert "pack.yaml" in authoring

    # Engine coordination skill explains result records and registered assignments.
    coord = _read("src/context_rail/skills/rail-coordinate/SKILL.md")
    assert "rail loop result" in coord
    assert "discovery" in coord.lower() or "commitless" in coord.lower()
    assert "rail loop report" in coord
    assert "duration_s" in coord
    assert "measure usage" in coord
    assert "`task` preset" in coord

    # AGENTS.md command inventory includes loop result and preserves invariants.
    agents_md = _read("AGENTS.md")
    assert "rail loop brief|candidate|result|review|report" in agents_md
    assert "never launches an agent" in agents_md

    # Engine-graph HTML guide includes loop result in #loop and matches len(TOOLS).
    from context_rail.gateway.tools import TOOLS

    engine_graph = _read("docs/context-rail-v3-engine-graph.html")
    loop_start = engine_graph.index('<section class="section" id="loop">')
    loop_end = engine_graph.index("</section>", loop_start)
    loop_section = engine_graph[loop_start:loop_end]
    assert "loop result" in loop_section
    assert "research/discovery" in loop_section.lower()
    assert f"{len(TOOLS)} tools" in engine_graph
    assert f"{len(TOOLS)}-tool tuple" in engine_graph

    # Every public skill keeps valid frontmatter with description <= 1024 chars.
    for path in sorted((_ROOT / "skills").rglob("SKILL.md")):
        raw = path.read_text(encoding="utf-8")
        assert raw.startswith("---\n"), path
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert isinstance(fm, dict)
        assert isinstance(fm.get("name"), str) and fm["name"]
        desc = fm.get("description")
        assert isinstance(desc, str) and 0 < len(desc) <= 1024, path
