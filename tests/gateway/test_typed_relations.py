"""Typed relations and containment cycles are refused at write time."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter
from context_rail.gateway.gateway import invoke
from helpers import init_store

STAMP = "2026-10-28T00:00:00Z"


def _add(store: Path, preset: str, title: str, rel: list[str] | None = None) -> dict:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or []},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result
    return result["value"]


def test_a_note_cannot_be_part_of_an_item(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    _add(store, "task", "A task")

    refused = invoke(
        "add",
        {"preset": "note", "title": "A note", "rel": ["part_of=a-task"]},
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert refused["error"]["details"]["relation"] == "part_of"


def test_a_link_with_a_wrong_source_primitive_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    _add(store, "task", "A task")
    note = _add(store, "note", "A note")

    refused = invoke(
        "link",
        {
            "src": note["slug"],
            "rel": "part_of",
            "dst": "a-task",
            "expect": note["etag"],
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"


def test_a_part_of_self_edge_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    task = _add(store, "task", "A task")

    refused = invoke(
        "link",
        {
            "src": task["slug"],
            "rel": "part_of",
            "dst": task["slug"],
            "expect": task["etag"],
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"


def test_a_part_of_cycle_is_refused(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    first = _add(store, "task", "First")
    second = _add(store, "task", "Second")

    linked = invoke(
        "link",
        {
            "src": "second",
            "rel": "part_of",
            "dst": "first",
            "expect": second["etag"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(linked), linked

    shown = invoke("show", {"slug": "first"}, store=store)
    refused = invoke(
        "link",
        {
            "src": "first",
            "rel": "part_of",
            "dst": "second",
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "CYCLE_DETECTED"


def test_doctor_reports_a_legacy_wrong_edge_as_a_warning(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    _add(store, "task", "A task")
    note = store / "notes" / "note" / "legacy.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\n"
        + dump_frontmatter(
            {
                "primitive": "note",
                "preset": "note",
                "title": "Legacy",
                "tags": [],
                "part_of": ["[[a-task]]"],
                "created": STAMP,
                "updated": STAMP,
            }
        )
        + "---\n# Legacy\n",
        encoding="utf-8",
        newline="\n",
    )

    report = invoke("doctor", {}, store=store, actor="human")

    assert report["ok"], report
    issue = next(
        item for item in report["value"]["issues"] if item["code"] == "relation_type"
    )
    assert issue["severity"] == "warning"
