"""Map, Next up and Health are screens, not redirects into the board.

The prototype proved these three read as screens of their own: the hierarchy
with live progress, the engine's queue with its reasons, and the store's health
with its digests. Each route used to bounce to the board, so the address named a
screen the app never rendered. They must read the compiled model and the store,
never a pack name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import context_rail

WEB = Path(context_rail.__file__).resolve().parents[2] / "web" / "src" / "react"
SCREENS = {"lens": ("MapPage", "map"), "now": ("QueuePage", "queue"), "doctor": ("HealthPage", "health")}


def _app() -> str:
    return (WEB / "App.tsx").read_text(encoding="utf-8")


@pytest.mark.parametrize("route", sorted(SCREENS))
def test_the_route_renders_a_page_not_a_redirect(route: str) -> None:
    component, module = SCREENS[route]
    body = _app()
    mapping = body.split("const pages:", 1)[1].split("};", 1)[0]
    entry = re.search(rf"^\s*{route}:\s*([^,\n]+)", mapping, re.MULTILINE)
    assert entry is not None, f"{route} is not in the page map"
    assert "redirectPage" not in entry.group(1), entry.group(1)
    assert component in entry.group(1), entry.group(1)
    assert component in body, f"{component} is never imported"
    assert (WEB / "pages" / f"{module}.tsx").is_file(), module


@pytest.mark.parametrize("route", sorted(SCREENS))
def test_the_screen_never_names_a_pack(route: str) -> None:
    _component, module = SCREENS[route]
    body = (WEB / "pages" / f"{module}.tsx").read_text(encoding="utf-8")
    for name in ("scrum", "waterfall", "video"):
        assert not re.search(rf"['\"]{name}['\"]", body), f"{module}: {name}"


def test_the_map_reads_the_declared_hierarchy_and_progress() -> None:
    body = (WEB / "pages" / "map.tsx").read_text(encoding="utf-8")
    assert "hierarchyLevels" in body or "hierarchy" in body
    assert "progressOf" in body, "the map invents its own completion"
    assert "container" in body, "the map does not use the declared containers"


def test_the_queue_reads_the_engine_queue_and_its_reasons() -> None:
    body = (WEB / "pages" / "queue.tsx").read_text(encoding="utf-8")
    assert "queueOf" in body, "the queue is not the engine's queue"
    assert "why" in body, "the queue hides why an item is eligible"
    assert "actions" in body, "the queue hides the declared actions"


def test_the_health_screen_shows_the_digests_it_reads() -> None:
    body = (WEB / "pages" / "health.tsx").read_text(encoding="utf-8")
    assert "digests" in body, "health never shows the approval digests"
    assert "semantic" in body and "presentation" in body


def test_the_health_screen_reads_the_health_api_contract() -> None:
    """/api/health emits {code, path, message, fix} and by_severity, never check/file/fix_hint."""
    body = (WEB / "pages" / "health.tsx").read_text(encoding="utf-8")
    for stale in ("issue.check", "issue.file", "issue.field", "issue.fix_hint", "doctor.counts", "Index cache"):
        assert stale not in body, f"health.tsx still reads stale field {stale!r}"
    for current in ("issue.code", "issue.path", "issue.fix", "by_severity", "error_code", "goal_slug"):
        assert current in body, f"health.tsx ignores {current!r}"


def test_g_shortcuts_have_one_source_of_truth() -> None:
    keyboard = (WEB / "keyboard.ts").read_text(encoding="utf-8")
    app = _app()
    settings = (WEB / "pages" / "settings-page.tsx").read_text(encoding="utf-8")
    assert "export const G_KEYS" in keyboard
    assert "G_KEYS" in app or "G_NAV_KEYS" in app, "App.tsx defines its own G-key map"
    assert "G_KEYS" in settings or "G_NAV_SHORTCUTS" in settings, "settings-page.tsx hardcodes G-key shortcuts"


@pytest.mark.parametrize("module", ["activity", "queue", "map"])
def test_url_filter_params_are_not_copied_into_stale_mount_state(module: str) -> None:
    body = (WEB / "pages" / f"{module}.tsx").read_text(encoding="utf-8")
    assert "useState(params.get(" not in body, f"{module}.tsx freezes URL params in mount state"


@pytest.mark.parametrize("route", ["now", "lens", "activity", "doctor"])
def test_the_sidebar_offers_every_store_screen(route: str) -> None:
    """A screen nobody can navigate to is not a screen."""
    body = _app()
    assert "aria-label=\"Understand\"" in body, "the store screens have no nav group"
    group = body.split('aria-label="Understand"', 1)[1].split("</section>", 1)[0]
    assert f"'{route}'" in group, f"{route} is missing from the sidebar"


@pytest.mark.parametrize(
    ("section", "route"),
    [("rdb-next", "now"), ("rdb-kicker\">Health", "doctor")],
)
def test_the_summary_links_to_the_screen_that_answers_it(section: str, route: str) -> None:
    """Summary previews the queue and health; each link must open that screen."""
    body = (WEB / "pages" / "dashboard.tsx").read_text(encoding="utf-8")
    block = body.split(section, 1)[1][:2600]
    assert f"navigate('{route}'" in block, f"{section} does not open {route}"
