"""Hostile values, graph refusals and the scoped-skill exception."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from context_rail.core.util import dump_frontmatter
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag
from helpers import init_nested_store, set_preset_placement

STAMP = "2026-10-28T00:00:00Z"


def _add(
    store: Path,
    preset: str,
    title: str,
    rel: list[str] | None = None,
    values: list[str] | None = None,
) -> dict[str, Any]:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or [], "set": values or []},
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def _tree(store: Path) -> dict[str, bytes]:
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file() and ".rail-local" not in path.parts
    }


def test_hostile_titles_stay_inside_the_store(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    for title in ("../../escape", "/etc/passwd", "back\\slash", "nul\x00byte"):
        created = _add(store, "phase", title)
        path = created["path"]
        assert path.startswith("items/phase/"), path
        assert ".." not in path.split("/")
        assert "\\" not in path
        assert "\x00" not in path
        assert (store / path).is_file()
    for title in ("CON", "com1", "nul", "x" * 200):
        result = invoke(
            "add", {"preset": "phase", "title": title}, store=store, actor="cto"
        )
        assert result["ok"] is False, title
        assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / "escape.md").exists()
    assert not (store.parent.parent / "escape.md").exists()


def test_a_field_value_with_separators_becomes_one_segment(tmp_path: Path) -> None:
    store = init_nested_store(
        tmp_path,
        layout={"note": "notes/{field:topic}/{slug}.md"},
        extra={"note": {"primitive": "note", "tags": [], "required": ["topic"]}},
    )

    created = _add(store, "note", "Hostile", values=["topic=../../etc/passwd"])

    assert created["path"] == "notes/etc-passwd/hostile.md"
    assert (store / created["path"]).is_file()


def test_a_reserved_or_overlong_literal_segment_refuses_at_load(
    tmp_path: Path,
) -> None:
    store = init_nested_store(tmp_path)
    for segment in ("CON", "com1", "x" * 81):
        set_preset_placement(store, task=f"{segment}/{{slug}}.md")
        result = invoke(
            "add",
            {"preset": "task", "title": "Refused"},
            store=store,
            actor="cto",
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "VALIDATION_FAILED"


def test_an_overlong_rendered_path_is_refused(tmp_path: Path) -> None:
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack
    from context_rail.store.placement import (
        PendingDocument,
        PlacementContext,
        PlacementError,
        PlacementRequest,
        expected_path,
    )

    store = init_nested_store(tmp_path)
    manifest = load_manifest(store)
    pack = load_pack("nested", store)
    sprint_path = f"items/phase/{'p' * 75}/{'s' * 75}.md"
    context = PlacementContext(
        manifest=manifest,
        pack=pack,
        pending=(
            PendingDocument(
                slug="sprint",
                primitive="item",
                preset="sprint",
                path=sprint_path,
            ),
        ),
    )

    with pytest.raises(PlacementError) as error:
        expected_path(
            context,
            PlacementRequest(
                primitive="item",
                preset="task",
                slug="t" * 75,
                relations={"part_of": ("sprint",)},
            ),
        )

    assert error.value.code == "placement_invalid"
    assert "longer than" in error.value.message


def test_a_second_part_of_target_refuses_without_writing(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    other = _add(store, "sprint", "Sprint 4", ["part_of=phase-1"])
    task = _add(store, "task", "Task", ["part_of=sprint-3"])
    before = (store / task["path"]).read_bytes()

    result = invoke(
        "link",
        {
            "src": "task",
            "rel": "part_of",
            "dst": other["slug"],
            "expect": task["etag"],
        },
        store=store,
        actor="cto",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "CONFLICT"
    assert (store / task["path"]).read_bytes() == before


def _task_doc(title: str, part_of: str) -> bytes:
    front = {
        "primitive": "item",
        "preset": "task",
        "title": title,
        "status": "todo",
        "tags": ["task"],
        "area": "general",
        "part_of": [f"[[{part_of}]]"],
        "created": STAMP,
        "updated": STAMP,
    }
    return (
        "---\n" + dump_frontmatter(front) + f"---\n# {title}\n"
    ).encode("utf-8")


def test_a_part_of_cycle_is_reported_and_never_moved(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    for slug, other in (("a", "b"), ("b", "a")):
        path = store / "items" / "task" / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_task_doc(slug.upper(), other))
    before = _tree(store)

    moved = invoke("relocate", {"dry_run": True}, store=store, actor="cto")

    assert moved["ok"] is True, moved
    assert moved["value"]["moves"] == []
    codes = {item["code"] for item in moved["value"]["skipped"]}
    assert "placement_cycle" in codes
    assert _tree(store) == before

    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"] is True, report
    assert {
        issue["code"] for issue in report["value"]["issues"]
    } >= {"placement_cycle"}


def test_a_scoped_skill_copy_is_not_moved_or_reported(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    scoped = store / "workspace" / "nested" / "skills" / "copy.md"
    scoped.parent.mkdir(parents=True, exist_ok=True)
    scoped.write_text(
        "---\n"
        "primitive: note\n"
        "preset: note\n"
        "title: Copy\n"
        "tags: []\n"
        f"created: '{STAMP}'\n"
        f"updated: '{STAMP}'\n"
        "---\n# Copy\n",
        encoding="utf-8",
        newline="\n",
    )

    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"] is True, report
    codes = {issue["code"] for issue in report["value"]["issues"]}
    assert "placement_mismatch" not in codes
    assert "placement_unresolved" not in codes

    moved = invoke("relocate", {"key": "scope"}, store=store, actor="cto")
    assert moved["ok"] is True, moved
    assert scoped.is_file()
    assert all(item["from"] != "workspace/nested/skills/copy.md" for item in moved["value"]["moves"])


class _Rows:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def execute(self, *_args: Any) -> "_Rows":
        return self

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class _Index:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self.conn = _Rows(rows)


def test_latest_record_uses_time_not_path_order(tmp_path: Path) -> None:
    from context_rail.doctor.checks import _latest_record

    store = tmp_path / "store"
    for rel, value in (
        ("records/z/new.md", "new"),
        ("records/a/old.md", "old"),
    ):
        path = store / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        front = {
            "primitive": "record",
            "preset": "event",
            "id": "01" + ("Z" if value == "new" else "A") * 24,
            "kind": "adapters.synced",
            "at": STAMP,
            "actor": "human",
            "about": [],
            "data": {"which": value},
        }
        path.write_bytes(("---\n" + dump_frontmatter(front) + "---\n").encode("utf-8"))

    ix = _Index([("records/z/new.md",), ("records/a/old.md",)])
    found = _latest_record(store, "adapters.synced", ix)

    assert found is not None
    assert found["data"] == {"which": "new"}


def test_a_record_id_is_never_taken_from_the_caller(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    result = invoke(
        "add",
        {
            "preset": "event",
            "title": "Hand set id",
            "set": ["id=../../escape"],
        },
        store=store,
        actor="cto",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_an_edited_record_keeps_its_bytes_and_digest(tmp_path: Path) -> None:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    task = _add(store, "task", "Task", ["part_of=sprint-3"])
    record = _add(store, "result", "UI", ["about=task"])
    before = (store / record["path"]).read_bytes()

    moved = invoke(
        "update",
        {"slug": "task", "set": ["area=backend"], "expect": task["etag"]},
        store=store,
        actor="cto",
    )
    assert moved["ok"] is True, moved

    after = store / (
        "records/items/phase/phase-1/sprint-3/backend/task/"
        f"{record['record']}.json"
    )
    assert after.read_bytes() == before
    assert etag(before) == etag(after.read_bytes())
