"""The shipped UI bundle carries no launcher or runtime-selection copy."""

from __future__ import annotations

from pathlib import Path

import context_rail

STATIC = Path(context_rail.__file__).parent / "ui" / "static"
FORBIDDEN = ("Dispatch to a worker", "launch defaults", "RAIL_RUNNER", "runner_models")


def test_ui_bundle_has_no_launcher_copy() -> None:
    files = [path for path in (STATIC / "assets").rglob("*") if path.is_file()]
    assert files

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for phrase in FORBIDDEN:
            assert phrase not in text, (path.name, phrase)