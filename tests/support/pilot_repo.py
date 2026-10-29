#!/usr/bin/env python3
"""Create the isolated pilot project and its local bare remote.

``py tests/support/pilot_repo.py --dir <temp>`` writes a tiny Python library
with one ``greet()`` function and a ``unittest`` suite that is the pilot's
acceptance Check, commits it on the branch git chose, and clones a bare
``file://`` remote beside it. Everything lands under ``--dir``; the source
repository is never touched, and the existing git identity is used as it is.

The generator only prepares a project. Onboarding, the picture, the acceptance
Checks and the approval are the human's steps, so the script prints them as the
next commands instead of running them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PilotError", "PilotRepo", "build_pilot", "main"]

PROJECT_NAME = "pilot"
REMOTE_NAME = "pilot-remote.git"

_GREET_MODULE = '''"""The pilot's library: one greeting function."""


def greet(name: str) -> str:
    """Return a greeting for ``name``."""
    return f"Hello, {name}!"
'''

_CLI_MODULE = '''"""The pilot's command line: print one greeting."""

import sys

from greet import greet


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print(greet(args[0] if args else "world"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_TEST_MODULE = '''"""The pilot's suite: the acceptance Check runs exactly this file."""

import unittest

from greet import greet


class GreetTest(unittest.TestCase):
    def test_greets_by_name(self) -> None:
        self.assertEqual(greet("Ada"), "Hello, Ada!")

    def test_greets_the_world_by_default(self) -> None:
        self.assertEqual(greet("world"), "Hello, world!")


if __name__ == "__main__":
    unittest.main()
'''

_README = """# Pilot

A tiny greetings library. The unittest suite under `tests/` is the Check the
rail verifies before an Item may close.
"""

_GITIGNORE = """__pycache__/
*.pyc
.rail-local/
"""

_NEXT_COMMANDS = """next steps (run from this checkout, with RAIL_ACTOR unset):
  1. rail --store {store} init --dir {project} --interview --answers-file <answers.json> \\
       --confirm "APPROVE charter"
  2. rail --store {store} check <slug> --target <item> --spec-file <acceptance.json> \\
       --expect absent --actor human --confirm "APPROVE <slug>"
  3. rail --store {store} approve charter --confirm "APPROVE charter" --confirmer <you>
  4. rail --store {store} ci
  5. rail --store {store} cto tick --key pilot-tick-1 --timeout 1800
model check: custom:Deepseek-V4-flash-0 (implementer, CTO) and
             custom:gemini-3.8-flash-LTN-0 (reviewer); never print a key.
"""


class PilotError(RuntimeError):
    """The environment cannot host a pilot: no identity or an occupied folder."""


@dataclass(frozen=True)
class PilotRepo:
    """The generated pilot: its project checkout, its store and its remote."""

    root: Path
    project: Path
    store: Path
    remote: Path
    branch: str

    @property
    def remote_url(self) -> str:
        """The ``file://`` URL the project's ``origin`` points at."""
        return self.remote.as_uri()


def build_pilot(root: Path) -> PilotRepo:
    """Create ``<root>/pilot`` and ``<root>/pilot-remote.git``; return both."""
    root = Path(root).absolute()
    project = root / PROJECT_NAME
    remote = root / REMOTE_NAME
    _require_identity()
    if project.exists() and any(project.iterdir()):
        raise PilotError(f"{project} is not empty; pass an unused --dir")
    if remote.exists() and any(remote.iterdir()):
        raise PilotError(f"{remote} is not empty; pass an unused --dir")
    project.mkdir(parents=True, exist_ok=True)
    _write_project(project)
    _git(root, "init", "--quiet", str(project))
    _git(project, "add", "-A")
    _git(project, "commit", "--quiet", "-m", "pilot: greetings library")
    branch = _git(project, "symbolic-ref", "--short", "HEAD").strip()
    _git(root, "init", "--quiet", "--bare", str(remote))
    _git(project, "remote", "add", "origin", remote.as_uri())
    _git(project, "push", "--quiet", "-u", "origin", branch)
    return PilotRepo(
        root=root,
        project=project,
        store=project / "context-rail",
        remote=remote,
        branch=branch,
    )


def _write_project(project: Path) -> None:
    package = project / "greet"
    tests = project / "tests"
    package.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    _write(project / "README.md", _README)
    _write(project / ".gitignore", _GITIGNORE)
    _write(package / "__init__.py", _GREET_MODULE)
    _write(package / "cli.py", _CLI_MODULE)
    _write(tests / "__init__.py", "")
    _write(tests / "test_greet.py", _TEST_MODULE)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _require_identity() -> None:
    """Refuse to build a pilot when git has no author identity to commit with.

    The identity is only read here: the generator never sets one, so a missing
    identity is an environment failure the operator has to fix.
    """
    for key in ("user.name", "user.email"):
        result = subprocess.run(
            ["git", "config", key], capture_output=True, text=True, encoding="utf-8"
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise PilotError(
                f"git has no {key}: configure an author identity before generating a pilot"
            )


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise PilotError(f"git {' '.join(args)} failed: {detail or 'no output'}")
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    """Build a pilot from ``--dir`` and print the commands the human runs next."""
    parser = argparse.ArgumentParser(
        description="Generate an isolated pilot project and a local bare remote."
    )
    parser.add_argument(
        "--dir", required=True, help="empty folder that receives pilot/ and the remote"
    )
    args = parser.parse_args(argv)
    try:
        pilot = build_pilot(Path(args.dir))
    except PilotError as error:
        print(f"pilot generator: {error}", file=sys.stderr)
        return 2
    print(f"project: {pilot.project}")
    print(f"store:   {pilot.store}")
    print(f"remote:  {pilot.remote_url}")
    print(f"branch:  {pilot.branch}")
    print(_NEXT_COMMANDS.format(store=pilot.store, project=pilot.project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
