"""Read-only surface commands: types, next, show, explain and why."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import TOOLS
from helpers import init_pack_store, init_store, workflow_pack_document


def _store(tmp_path: Path) -> Path:
    store = init_pack_store(tmp_path, workflow_pack_document())
    assert is_ok(
        invoke("add", {"preset": "task", "title": "A task"}, store=store, actor="cto")
    )
    assert is_ok(
        invoke("add", {"preset": "bug", "title": "A bug"}, store=store, actor="cto")
    )
    return store


def test_types_returns_the_bounded_catalog(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = invoke("types", {"limit": 4000}, store=store, actor="cto")

    assert is_ok(result), result
    names = {entry["name"] for entry in result["value"]["items"]}
    assert {"task", "bug"} <= names
    assert result["value"]["omitted"] >= 0


def test_next_returns_the_queue_for_the_cto_and_a_card_for_a_worker(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    queue = invoke("next", {}, store=store, actor="cto")
    worker = invoke("next", {"item": "a-bug"}, store=store, actor="implementer")

    assert is_ok(queue), queue
    assert {"a-bug", "a-task"} <= set(queue["value"]["queue"])
    assert is_ok(worker), worker
    assert worker["value"]["slug"] == "a-bug"
    assert worker["value"]["workflow"] == "bugfix"


def test_next_does_not_offer_a_blocked_item(approved_store: Path) -> None:
    added = invoke(
        "add",
        {
            "preset": "task",
            "title": "Blocked task",
            "rel": ["part_of=ship-invoicing"],
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]
    shown = invoke("show", {"slug": slug}, store=approved_store, actor="cto")
    doing = invoke(
        "move",
        {"slug": slug, "status": "doing", "expect": shown["value"]["doc"]["etag"]},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(doing), doing
    blocked = invoke(
        "move",
        {
            "slug": slug,
            "status": "blocked",
            "expect": doing["value"]["etag"],
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(blocked), blocked

    queue = invoke("next", {}, store=approved_store, actor="cto")
    status = invoke("status", {}, store=approved_store, actor="cto")

    assert slug not in queue["value"]["queue"]
    assert slug in status["value"]["blocked"]


def test_next_excludes_an_open_dependency_and_names_it(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path)
    for title in ("Blocker task", "Blocked task"):
        added = invoke(
            "add", {"preset": "task", "title": title}, store=store, actor="cto"
        )
        assert is_ok(added), added
    shown = invoke("show", {"slug": "blocked-task"}, store=store, actor="cto")
    linked = invoke(
        "link",
        {
            "src": "blocked-task",
            "rel": "depends_on",
            "dst": "blocker-task",
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(linked), linked

    queue = invoke("next", {}, store=store, actor="cto")
    reason = invoke("why", {"slug": "blocked-task"}, store=store, actor="cto")

    assert queue["value"]["queue"] == ["blocker-task"]
    assert queue["value"]["blocked"] == {"blocked-task": "depends_on:blocker-task"}
    assert reason["value"]["blocked"] == "depends_on:blocker-task"
    assert reason["value"]["command"] is None


def test_show_can_disambiguate_a_shared_skill_slug(approved_store: Path) -> None:
    ambiguous = invoke("show", {"slug": "test-first"}, store=approved_store, actor="cto")
    shown = invoke(
        "show",
        {"slug": "test-first", "path": "workspace/cto/skills/test-first.md"},
        store=approved_store,
        actor="cto",
    )

    assert ambiguous["ok"] is False
    assert ambiguous["error"]["code"] == "CONFLICT"
    assert is_ok(shown), shown
    assert shown["value"]["doc"]["path"] == "workspace/cto/skills/test-first.md"
    wrong = invoke(
        "show",
        {"slug": "charter", "path": "workspace/cto/skills/test-first.md"},
        store=approved_store,
        actor="cto",
    )
    assert wrong["ok"] is False
    assert wrong["error"]["code"] == "NOT_FOUND"


def test_a_worker_without_an_item_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = invoke("next", {}, store=store, actor="implementer")

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_explain_and_why_answer_from_the_pack(tmp_path: Path) -> None:
    store = _store(tmp_path)

    explained = invoke("explain", {"name": "bugfix"}, store=store, actor="cto")
    reason = invoke("why", {"slug": "a-bug"}, store=store, actor="cto")

    assert is_ok(explained), explained
    assert explained["value"]["kind"] == "workflow"
    assert is_ok(reason), reason
    assert reason["value"]["workflow"] == "bugfix"
    assert reason["value"]["command"].startswith("rail change move")


def test_check_list_is_a_read_alias_for_checks(tmp_path: Path) -> None:
    store = _store(tmp_path)

    listed = invoke("check list", {}, store=store, actor="cto")

    assert is_ok(listed), listed
    assert listed["value"] == {"items": [], "total": 0, "omitted": 0}


def test_show_carries_the_workflow_card(tmp_path: Path) -> None:
    store = _store(tmp_path)

    shown = invoke("show", {"slug": "a-bug"}, store=store, actor="cto")

    assert is_ok(shown), shown
    assert shown["value"]["card"]["workflow"] == "bugfix"


def test_the_surface_commands_are_read_only() -> None:
    by_name = {tool.name: tool for tool in TOOLS}

    for name in ("types", "next", "explain", "why", "show"):
        assert by_name[name].permission == "read"
