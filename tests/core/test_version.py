"""The package version and the suite-wide daemon switch."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import context_rail


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert context_rail.__version__ == project["version"]


def test_the_suite_runs_with_the_daemon_off() -> None:
    assert os.environ["RAIL_NO_DAEMON"] == "1"


def test_daemon_enabled_clears_the_switch(daemon_enabled: None) -> None:
    assert "RAIL_NO_DAEMON" not in os.environ
