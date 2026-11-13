"""The agent launcher is gone: its commands answer with a tombstone."""

from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace

import pytest

from context_rail.core.tool_names import REMOVED_COMMANDS, REMOVED_FLAGS
from context_rail.gateway.gateway import invoke
from context_rail.store.location import local_dir
from context_rail.store.lock import take_exclusive
from helpers import refusal, run_cli

HUMAN = {"RAIL_ACTOR": "human"}

LAUNCHER_MODULES = (
    "context_rail.runner.droid",
    "context_rail.runner.protocol",
    "context_rail.runner.dispatch",
    "context_rail.runner.control",
    "context_rail.runner.merge",
    "context_rail.gateway.handlers.dispatch",
    "context_rail.store.github_sync",
)


def test_removed_commands_name_their_replacements() -> None:
    assert {"dispatch", "loop dispatch", "merge", "loop merge"} <= set(REMOVED_COMMANDS)
    assert {"cto tick", "loop tick"} <= set(REMOVED_COMMANDS)
    assert not {"review", "loop review"} & set(REMOVED_COMMANDS)
    for replacement in REMOVED_COMMANDS.values():
        assert "rail loop brief" in replacement
        assert "rail loop candidate" in replacement


@pytest.mark.parametrize("name", sorted(REMOVED_COMMANDS))
def test_removed_commands_answer_with_tombstone(
    name: str, leaf_item: SimpleNamespace
) -> None:
    result = invoke(name, {}, store=leaf_item.store, actor="human")

    assert refusal(result) == ("VALIDATION_FAILED", "command_removed")
    error = result["error"]
    assert error["details"]["replacement"] == REMOVED_COMMANDS[name]
    assert "rail loop candidate" in error["message"]


@pytest.mark.parametrize("words", [name.split() for name in sorted(REMOVED_COMMANDS)])
def test_cli_removed_command_prints_tombstone(
    words: list[str], leaf_item: SimpleNamespace
) -> None:
    proc = run_cli(leaf_item.root, *words, "task-1", store=leaf_item.store)

    assert proc.returncode == 1
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["reason"] == "command_removed"
    assert "rail loop brief" in error["details"]["replacement"]


@pytest.mark.parametrize("module", LAUNCHER_MODULES)
def test_launcher_modules_are_gone(module: str) -> None:
    assert importlib.util.find_spec(module) is None


@pytest.mark.parametrize("name", sorted(REMOVED_FLAGS))
def test_the_old_repair_flags_print_their_replacement(
    name: str, leaf_item: SimpleNamespace
) -> None:
    proc = run_cli(leaf_item.root, *name.split(), store=leaf_item.store, env=HUMAN)

    assert proc.returncode == 1
    error = json.loads(proc.stdout)["error"]
    assert error["details"]["reason"] == "command_removed"
    assert "rail ops repair" in error["details"]["replacement"]


def test_the_gateway_answers_the_old_repair_flag(
    leaf_item: SimpleNamespace,
) -> None:
    result = invoke("doctor --repair", {}, store=leaf_item.store, actor="human")

    assert refusal(result) == ("VALIDATION_FAILED", "command_removed")


def test_two_syncs_cannot_overlap(two_clones: SimpleNamespace) -> None:
    release = take_exclusive(local_dir(two_clones.store_a) / "sync.lock")
    try:
        result = invoke("sync", {}, store=two_clones.store_a, actor="human")
    finally:
        release()

    assert refusal(result)[0] == "CONFLICT"
    assert "another sync" in result["error"]["message"]
    assert invoke("sync", {}, store=two_clones.store_a, actor="human")["ok"] is True
