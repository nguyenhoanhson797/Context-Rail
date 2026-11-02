"""A bare rail invocation points at the onboarding entry point."""

from __future__ import annotations

import json
from pathlib import Path

from helpers import run_cli


def test_bare_rail_points_at_init(tmp_path: Path) -> None:
    proc = run_cli(tmp_path)

    assert proc.returncode == 0
    assert json.loads(proc.stdout)["value"]["next"] == "rail init"
