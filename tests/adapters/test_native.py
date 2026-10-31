from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import invoke
from helpers import latest_record


def _call(store: Path, *harness: str, **extra) -> dict:
    return invoke(
        "adapters sync", {"harness": list(harness), **extra}, store=store, actor="cto"
    )


def test_native_harnesses_write_their_own_files(approved_store: Path) -> None:
    result = _call(approved_store, "claude", "cursor", "gemini")
    assert result["ok"] is True, result
    written = set(result["value"]["written"])
    root = approved_store.parent
    expected = {
        "CLAUDE.md",
        ".claude/agents/cto.md",
        ".claude/agents/implementer.md",
        ".claude/agents/reviewer.md",
        ".cursor/rules/context-rail.mdc",
        "GEMINI.md",
    }
    assert expected <= written
    for rel in expected:
        assert (root / rel).is_file(), rel
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "CONTEXT-RAIL-START" in claude
    agent = (root / ".claude/agents/cto.md").read_text(encoding="utf-8")
    assert agent.startswith("---\nname: context-rail-cto\n")
    assert "model:" in agent
    assert "CONTEXT-RAIL-START" in agent
    cursor = (root / ".cursor/rules/context-rail.mdc").read_text(encoding="utf-8")
    assert cursor.startswith("---\ndescription:")
    assert "alwaysApply: true" in cursor

    record = latest_record(approved_store, "adapters.synced")
    assert record["data"]["harnesses"] == ["claude", "cursor", "gemini"]
    assert expected <= set(record["data"]["paths"])


def test_a_request_without_harness_keeps_the_stage_two_output(
    approved_store: Path,
) -> None:
    result = invoke("adapters sync", {}, store=approved_store, actor="cto")
    assert result["ok"] is True, result
    record = latest_record(approved_store, "adapters.synced")
    assert record["data"]["harnesses"] == []
    paths = set(record["data"]["paths"])
    assert "AGENTS.md" in paths
    assert not any(path.startswith(".claude/") for path in paths)
    assert "CLAUDE.md" not in paths


def test_a_second_sync_is_unchanged_and_an_unknown_harness_refuses(
    approved_store: Path,
) -> None:
    assert _call(approved_store, "claude", "gemini")["ok"] is True
    again = _call(approved_store, "claude", "gemini")
    assert again["ok"] is True
    assert again["value"]["written"] == []
    assert again["value"]["unchanged"]

    before = sorted(
        path.relative_to(approved_store.parent).as_posix()
        for path in approved_store.parent.rglob("*")
        if path.is_file()
    )
    refused = _call(approved_store, "vim")
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    after = sorted(
        path.relative_to(approved_store.parent).as_posix()
        for path in approved_store.parent.rglob("*")
        if path.is_file()
    )
    assert before == after


def test_a_hand_edited_native_file_is_a_conflict_and_doctor_sees_it(
    approved_store: Path,
) -> None:
    assert _call(approved_store, "cursor")["ok"] is True
    path = approved_store.parent / ".cursor/rules/context-rail.mdc"
    text = path.read_text(encoding="utf-8")
    body = text.split("CONTEXT-RAIL-START", 1)[1]
    path.write_text(
        "---\ndescription: Context Rail store rules\nalwaysApply: true\n---\n"
        "<!-- CONTEXT-RAIL-START -->"
        + body.replace("Context Rail", "Changed by hand", 1),
        encoding="utf-8",
        newline="\n",
    )
    refused = _call(approved_store, "cursor")
    assert refused["error"]["code"] == "CONFLICT"

    report = invoke("doctor", {}, store=approved_store, actor="human")
    codes = [issue["code"] for issue in report["value"]["issues"]]
    assert "adapter_modified" in codes
