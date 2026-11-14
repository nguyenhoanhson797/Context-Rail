"""The generated agent files name only commands this engine has.

An agent trusts the managed block in `AGENTS.md` and the mirrored harness
files. A command that no longer exists there is worse than a stale page: the
agent runs it, reads a tombstone, and reports work it never did. These tests
hold every generated command to the live tool table and refuse the launcher's
vocabulary anywhere in the payload.
"""

from __future__ import annotations

from pathlib import Path

from command_names import rail_commands, unknown_rail_commands

from context_rail.gateway.gateway import invoke

#: Words the deleted launcher used. None may appear as instruction in a
#: generated file, in any casing.
_LAUNCHER_WORDS = (
    "loop dispatch",
    "loop merge",
    "loop tick",
    "droid exec",
    "run record",
    "worktree",
)


def _payload(approved_store: Path, harness: list[str]) -> list[tuple[str, str]]:
    """Every generated file's text, keyed by its adapter-relative path."""
    result = invoke(
        "adapters sync", {"harness": harness}, store=approved_store, actor="cto"
    )
    assert result["ok"] is True, result
    root = approved_store.parent
    return [(rel, (root / rel).read_text(encoding="utf-8")) for rel in result["value"]["written"]]


def test_every_generated_command_reaches_a_tool(approved_store: Path) -> None:
    files = _payload(approved_store, ["agents", "claude", "cursor", "gemini"])
    assert files
    unknown: list[tuple[str, str]] = []
    seen = 0
    for rel, body in files:
        if rel.endswith("SKILL.md"):
            continue
        seen += len(rail_commands(body))
        unknown.extend((rel, command) for command in unknown_rail_commands(body))
    assert seen > 20, f"only {seen} commands found; the payload lost its commands"
    assert unknown == [], unknown


def test_generated_files_name_no_launcher_command(approved_store: Path) -> None:
    files = _payload(approved_store, ["agents", "claude", "cursor", "gemini"])
    for rel, body in files:
        lowered = body.lower()
        for word in _LAUNCHER_WORDS:
            assert word not in lowered, (rel, word)


def test_the_managed_block_names_the_record_loop(approved_store: Path) -> None:
    files = dict(_payload(approved_store, ["claude"]))
    body = files["CLAUDE.md"]
    for step in (
        "rail loop brief",
        "rail loop candidate",
        "rail check run",
        "rail loop review",
        "rail change move",
        "rail loop report",
    ):
        assert step in body, step
    assert "The rail does not run agents" in body
