from __future__ import annotations

from helpers import init_store
from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import TOOLS


def test_tool_table_has_no_sprint_or_phase_entry() -> None:
    names = {tool.name for tool in TOOLS}
    assert not any(name.startswith(("sprint", "phase")) for name in names)


def test_renaming_a_sprint_tag_changes_no_behaviour(tmp_path) -> None:
    store = init_store(tmp_path, pack="scrum")
    sprint = invoke("add", {"preset": "sprint", "title": "Sprint 1", "tag": ["sprint"]}, store=store, actor="cto")
    assert sprint["ok"] is True
    before = invoke("status", {}, store=store, actor="cto")["value"]
    edited = invoke("update", {"slug": sprint["value"]["slug"], "tag": ["-sprint", "+iteration"], "expect": sprint["value"]["etag"]}, store=store, actor="cto")
    assert edited["ok"] is True
    after = invoke("status", {}, store=store, actor="cto")["value"]
    assert after["executable"] == before["executable"] and after["blocked"] == before["blocked"]


def test_story_under_a_sprint_is_blocked_by_an_unfinished_dependency(tmp_path) -> None:
    store = init_store(tmp_path, pack="scrum")
    sprint = invoke("add", {"preset": "sprint", "title": "Sprint 1"}, store=store, actor="cto")["value"]
    first = invoke("add", {"preset": "task", "title": "First"}, store=store, actor="cto")["value"]
    second = invoke("add", {"preset": "story", "title": "Second", "rel": [f"part_of={sprint['slug']}", f"depends_on={first['slug']}"]}, store=store, actor="cto")["value"]
    blocked = invoke("status", {}, store=store, actor="cto")["value"]["blocked"]
    assert blocked[second["slug"]] == f"depends_on:{first['slug']}"
