"""The public HTML guides describe this engine, not the deleted launcher.

`docs/*.html` is linked from the README and read by people deciding whether to
adopt the store. The Markdown gates never covered it, so the pages kept
describing a launcher that no longer exists. A guide may name a removed command
only to say it was removed and how to migrate.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDES = sorted((ROOT / "docs").glob("*.html"))

#: Commands the launcher owned or that were removed from the surface. A guide
#: may mention one inside a migration sentence, which always carries one of
#: these words nearby.
_REMOVED = (
    "loop dispatch",
    "loop merge",
    "loop tick",
    "droid exec",
    "doctor --repair",
    "doctor --fix",
)
_MIGRATION = re.compile(
    r"removed|no longer|was renamed|delete|deleted|upgrade|migrat|used to|"
    r"replaced|not part of|gone",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]*[.!?\n]")


def _guide_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_there_are_guides_to_check() -> None:
    assert GUIDES, "no docs/*.html guides found"


def test_guides_never_instruct_a_removed_command() -> None:
    offenders: list[tuple[str, str]] = []
    for path in GUIDES:
        body = _guide_text(path)
        for phrase in _REMOVED:
            start = 0
            while (index := body.lower().find(phrase, start)) != -1:
                start = index + 1
                line = body.count("\n", 0, index) + 1
                window = body[max(0, index - 400) : index + 400]
                if not _MIGRATION.search(window):
                    offenders.append((f"{path.name}:{line}", phrase))
    assert offenders == [], offenders


def test_guides_say_the_rail_does_not_run_agents() -> None:
    for path in GUIDES:
        body = _guide_text(path).lower()
        if "loop" not in body:
            continue
        assert (
            "does not run agents" in body
            or "never launches an agent" in body
            or "does not launch an agent" in body
            or "never launch an agent" in body
        ), path.name


def test_guides_name_no_retry_policy() -> None:
    offenders = []
    for path in GUIDES:
        body = _guide_text(path)
        for match in re.finditer(r"one retry|retries|retry cap|escalate", body, re.IGNORECASE):
            line = body.count("\n", 0, match.start()) + 1
            window = body[max(0, match.start() - 300) : match.start() + 300]
            if not _MIGRATION.search(window):
                offenders.append((f"{path.name}:{line}", match.group(0)))
    assert offenders == [], offenders


def test_engine_graph_tool_count_matches_the_tool_table() -> None:
    from context_rail.gateway.tools import TOOLS

    expected = len(TOOLS)
    body = _guide_text(ROOT / "docs" / "context-rail-v3-engine-graph.html")
    counts = [int(match) for match in re.findall(r"\b(\d+) tools\b", body)]
    assert counts, "engine-graph guide does not state the tool count"
    assert all(count == expected for count in counts), (counts, expected)
