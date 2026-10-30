"""The printed pilot runbook is a contract: every printed command must succeed."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from helpers import run_cli

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "tests" / "support" / "pilot_repo.py"
_STEP = re.compile(r"^\s*\d+\.\s+(rail\b.*)$")
_GLOBAL_FLAGS = ("--store", "--actor")

_ANSWERS: dict[str, Any] = {
    "schema": 1,
    "pack": "small",
    "goal": {"title": "Ship greetings", "body": "Greet every visitor by name."},
    "vision": {
        "title": "Greetings everywhere",
        "body": "Every project greets visitors without a human in the loop.",
    },
    "scope": [
        {
            "title": "Greet by name",
            "body": "Return a greeting for the name it is given.",
            "priority": "must",
        },
        {
            "title": "Greet default",
            "body": "Greet the world when no name is supplied.",
            "priority": "should",
        },
    ],
    "wont": [{"title": "Localization", "reason": "A translation product does it better."}],
    "charter": {"schema": 1},
}

_ACCEPTANCE: dict[str, Any] = {
    "schema": 1,
    "mode": "cmd",
    "acceptance": True,
    "cmd": ["py", "-m", "unittest", "discover", "-s", "tests"],
    "inputs": ["greet/*.py", "tests/*.py"],
    "timeout_s": 120,
}


def _generate(root: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    completed = subprocess.run(
        [sys.executable, str(_GENERATOR), "--dir", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_REPO_ROOT,
    )
    facts: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        label, separator, value = line.partition(":")
        if separator and label.strip() in {"project", "store", "remote", "branch"}:
            facts[label.strip()] = value.strip()
    return completed, facts


def _runbook_lines(stdout: str) -> list[str]:
    """The printed ``rail ...`` steps, in order, ignoring the model summary."""
    steps: list[str] = []
    for line in stdout.splitlines():
        match = _STEP.match(line)
        if match is not None:
            steps.append(match.group(1).strip())
    return steps


def _substitute(command: str, values: dict[str, str]) -> str:
    for placeholder, replacement in values.items():
        command = command.replace(placeholder, replacement)
    return command


def _argv(command: str) -> list[str]:
    tokens = _split(command)
    assert tokens and tokens[0] == "rail", tokens
    return tokens[1:]


def _split(command: str) -> list[str]:
    """Split one printed command, honouring double quotes but never escaping.

    ``shlex`` would treat the Windows path separators in a substituted path as
    escapes, so this keeps backslashes literal.
    """
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for char in command.strip():
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _subcommand_index(tokens: list[str]) -> int:
    """Where the subcommand begins: the first token that is not a value of a flag."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--store" or token == "--actor":
            index += 2
            continue
        return index
    return index


def test_the_printed_runbook_uses_one_init_mode_and_global_flags_first(
    tmp_path: Path,
) -> None:
    completed, _facts = _generate(tmp_path / "contract-root")
    assert completed.returncode == 0, completed.stderr

    steps = _runbook_lines(completed.stdout)
    assert len(steps) == 5, steps

    init_step = steps[0]
    modes = [flag for flag in ("--interview", "--answers-file", "--draft-file") if flag in init_step]
    assert len(modes) == 1 and modes[0] == "--answers-file", modes

    for step in steps:
        tokens = _argv(step)
        sub = _subcommand_index(tokens)
        for flag in _GLOBAL_FLAGS:
            if flag in tokens:
                assert tokens.index(flag) < sub, (flag, tokens)


def test_every_printed_runbook_command_succeeds(
    tmp_path: Path, fake_droid: Any, droid_env: dict[str, str]
) -> None:
    root = tmp_path / "runbook-root"
    completed, facts = _generate(root)
    assert completed.returncode == 0, completed.stderr
    project = Path(facts["project"])
    store = Path(facts["store"])

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_ANSWERS, indent=2), encoding="utf-8", newline="\n")
    spec = tmp_path / "acceptance.json"
    spec.write_text(json.dumps(_ACCEPTANCE, indent=2), encoding="utf-8", newline="\n")

    values = {
        "<answers.json>": str(answers),
        "<acceptance.json>": str(spec),
        "<slug>": "acceptance-unit",
        "<item>": "greet-by-name",
        "<you>": "validator",
    }

    for index, step in enumerate(_runbook_lines(completed.stdout), start=1):
        argv = _argv(_substitute(step, values))
        run = run_cli(project, *argv)
        assert run.returncode == 0, (index, step, run.stdout, run.stderr)
        payload = json.loads(run.stdout)
        assert payload.get("ok") is True, (index, step, payload)

    assert store.is_dir()
    assert (store / "records").is_dir()
