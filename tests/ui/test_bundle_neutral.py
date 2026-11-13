"""The shipped UI bundle matches the neutral engine and the source that built it."""

from __future__ import annotations

import re
from pathlib import Path

import context_rail

STATIC = Path(context_rail.__file__).parent / "ui" / "static"
FORBIDDEN = (
    "Dispatch to a worker",
    "launch defaults",
    "RAIL_RUNNER",
    "runner_models",
    "droid exec",
    "loop tick",
    "Recorded runs",
)
# Views and screens deleted with the launcher. If one reappears in a build, the
# bundle was not built from this tree.
DELETED = ("context_graph", "ui_adapter_diagnostics", "adapter_diagnostics", "task-workspace")
# Present in every build; their absence means a stale bundle.
REQUIRED = ("Keyboard shortcuts", "Configuration", "Search or read a tool")


def _assets() -> list[Path]:
    files = [path for path in (STATIC / "assets").rglob("*") if path.is_file()]
    assert files
    return files


def test_ui_bundle_has_no_launcher_copy() -> None:
    for path in _assets():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for phrase in FORBIDDEN + DELETED:
            assert phrase not in text, (path.name, phrase)


def test_ui_bundle_carries_the_screens_the_source_has() -> None:
    scripts = [path for path in _assets() if path.suffix == ".js"]
    assert scripts
    body = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in scripts)
    for phrase in REQUIRED:
        assert phrase in body, phrase


def test_index_html_points_at_assets_that_exist() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    referenced = re.findall(r"assets/([A-Za-z0-9._-]+)", html)
    assert referenced, html
    present = {path.name for path in (STATIC / "assets").iterdir()}
    missing = [name for name in referenced if name not in present]
    assert missing == [], missing
