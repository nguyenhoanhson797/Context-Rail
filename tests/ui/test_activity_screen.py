"""Catch up is a real screen, and its feed is the store's own event log.

The route used to redirect to the board, so the address bar named a screen the
app never rendered. The feed must come from the recorded events (`kind`, `at`,
`actor` and the `about` relation), never from a client-side guess about what
changed, and it must not name a pack to decide what an event looks like.
"""

from __future__ import annotations

import re
from pathlib import Path

import context_rail

WEB = Path(context_rail.__file__).resolve().parents[2] / "web" / "src" / "react"


def _app() -> str:
    return (WEB / "App.tsx").read_text(encoding="utf-8")


def _activity() -> str:
    return (WEB / "pages" / "activity.tsx").read_text(encoding="utf-8")


def test_the_activity_route_renders_a_page_not_a_redirect() -> None:
    body = _app()
    mapping = body.split("const pages:", 1)[1].split("};", 1)[0]
    entry = re.search(r"^\s*activity:\s*([^,\n]+)", mapping, re.MULTILINE)
    assert entry is not None, "the activity route is not in the page map"
    assert "redirectPage" not in entry.group(1), entry.group(1)
    assert "ActivityPage" in entry.group(1), entry.group(1)
    assert "ActivityPage" in body, "the page component is never imported"


def test_the_feed_reads_the_recorded_event_fields() -> None:
    body = _activity()
    for field in ("kind", "at", "actor"):
        assert re.search(rf"\b{field}\b", body), f"the feed ignores {field!r}"
    assert "relations" in body, "the feed never resolves the event subject"
    assert "primitive" in body, "the feed does not select records structurally"


def test_the_feed_never_invents_a_pack_branch() -> None:
    body = _activity()
    for name in ("scrum", "waterfall", "video"):
        assert not re.search(rf"['\"]{name}['\"]", body), name
