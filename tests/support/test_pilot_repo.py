"""The pilot generator: an isolated project, a local bare remote, next commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import make_pilot_repo
from support.pilot_repo import PilotError, build_pilot

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(where: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, encoding="utf-8"
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_pilot_generator_creates_an_isolated_repo(tmp_path: Path) -> None:
    pilot = make_pilot_repo(tmp_path / "pilot-root")

    assert pilot.remote.as_uri().startswith("file://")
    assert pilot.store.parent == pilot.project
    assert pilot.root == (tmp_path / "pilot-root").absolute()
    log = _git(pilot.project, "log", "--oneline")
    assert log.splitlines()
    assert pilot.branch
    url = _git(pilot.project, "remote", "get-url", "origin")
    assert url == pilot.remote.as_uri()
    assert _git(pilot.remote, "rev-parse", "--is-bare-repository") == "true"
    assert _git(pilot.remote, "rev-parse", f"refs/heads/{pilot.branch}") == _git(
        pilot.project, "rev-parse", "HEAD"
    )


def test_pilot_generator_writes_a_library_and_a_unittest_suite(tmp_path: Path) -> None:
    pilot = make_pilot_repo(tmp_path / "pilot-root")

    assert (pilot.project / "greet" / "__init__.py").is_file()
    suite = pilot.project / "tests" / "test_greet.py"
    assert suite.is_file()
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=pilot.project,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr


def test_pilot_generator_prints_the_next_commands(tmp_path: Path) -> None:
    root = tmp_path / "cli-root"
    completed = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "tests" / "support" / "pilot_repo.py"), "--dir", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    text = completed.stdout
    assert "rail --store" in text
    assert "approve charter --confirm" in text
    assert "cto tick --key pilot-tick-1" in text
    assert "custom:Deepseek-V4-flash-0" in text
    assert "custom:gemini-3.8-flash-LTN-0" in text
    assert (root / "pilot" / "README.md").is_file()


def test_the_source_repo_gains_no_pilot_file(tmp_path: Path) -> None:
    before = _git(_REPO_ROOT, "status", "--porcelain")
    make_pilot_repo(tmp_path / "pilot-root")
    assert _git(_REPO_ROOT, "status", "--porcelain") == before
    assert not (_REPO_ROOT / "pilot").exists()


def test_a_missing_identity_is_an_environment_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "system-gitconfig"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))

    with pytest.raises(PilotError) as raised:
        build_pilot(tmp_path / "no-identity")

    assert "identity" in str(raised.value).lower() or "user." in str(raised.value)
    assert not (tmp_path / "no-identity" / "pilot" / ".git").exists()
