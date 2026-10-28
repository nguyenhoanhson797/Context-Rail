"""Gateway, tool table and handler behaviour through the Python API."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter, load_yaml
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import record_digest
from helpers import touch_doc

STAMP = "2026-10-28T00:00:00Z"
RECORD_KEYS = {"id", "primitive", "preset", "kind", "at", "actor", "about", "data", "digest"}


# ── helpers ───────────────────────────────────────────────────────────────


def _init(tmp_path: Path, **args: Any) -> Path:
    result = invoke("init", {"dir": str(tmp_path), **args}, actor="human")
    assert is_ok(result), result
    return Path(result["value"]["store"])


def _files(store: Path, folder: str = ".") -> set[str]:
    root = store / folder
    return {
        path.relative_to(store).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(store).as_posix().startswith(".rail-local/")
    }


def _front(store: Path, rel: str) -> tuple[dict[str, Any], str]:
    text = (store / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
    _, rest = text.split("---\n", 1)
    raw, body = rest.split("---\n", 1)
    return load_yaml(raw), body


def _record_payload(store: Path, rel: str) -> dict[str, Any]:
    return json.loads((store / rel).read_text(encoding="utf-8"))


def _new_record(store: Path, before: set[str]) -> dict[str, Any]:
    new = sorted(_files(store, "records") - before)
    assert len(new) == 1, new
    rel = new[0]
    if rel.endswith(".json"):
        return _record_payload(store, rel)
    front, _ = _front(store, rel)
    return front


def _etag(store: Path, slug: str) -> str:
    result = invoke("show", {"slug": slug}, store=store)
    assert is_ok(result), result
    return result["value"]["doc"]["etag"]


def _write_doc(
    store: Path, rel: str, front: dict[str, Any], body: str = ""
) -> None:
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )


def _task_front(
    title: str,
    *,
    status: str = "todo",
    part_of: str | None = None,
    depends_on: str | None = None,
    created: str = STAMP,
    updated: str = STAMP,
) -> dict[str, Any]:
    front: dict[str, Any] = {
        "primitive": "item",
        "preset": "task",
        "title": title,
        "status": status,
        "tags": ["task"],
        "created": created,
        "updated": updated,
    }
    if part_of is not None:
        front["part_of"] = [f"[[{part_of}]]"]
    if depends_on is not None:
        front["depends_on"] = [f"[[{depends_on}]]"]
    return front


# ── init ──────────────────────────────────────────────────────────────────


def test_init_writes_manifest_folders_and_gitignore(tmp_path):
    result = invoke("init", {"dir": str(tmp_path)}, actor="human")

    assert is_ok(result), result
    store = tmp_path / "context-rail"
    assert Path(result["value"]["store"]) == store.resolve()
    manifest = load_yaml((store / "rail.yaml").read_text(encoding="utf-8"))
    assert manifest == {
        "schema": 3,
        "pack": "small",
        "placement": {
            "item": "items/{preset}/{slug}.md",
            "note": "notes/{preset}/{slug}.md",
            "check": "checks/{slug}.md",
            "record": "records/{yyyy}-{mm}/{id}.json",
            "actor": "workspace/profiles/{slug}/profile.md",
            "policy": "policies/{slug}.md",
        },
        "presets": {
            "decision": "records/decisions/{slug}.md",
            "charter": "charter.md",
            "goal": "project/{slug}.md",
            "vision": "project/{slug}.md",
            "scope": "project/{slug}.md",
            "wont": "project/{slug}.md",
            "skill": "workspace/skills/{slug}.md",
            "cto": "workspace/cto/profile.md",
            "profile": "workspace/profiles/{slug}/profile.md",
        },
    }
    for rel in (
        "items",
        "notes",
        "checks",
        "records",
        "policies",
        "workspace/profiles",
        "workspace/skills",
        "packs",
        "templates",
    ):
        assert (store / rel).is_dir(), rel
    top = {path.name for path in store.iterdir() if path.is_dir()}
    assert top == {
        "items",
        "notes",
        "checks",
        "records",
        "policies",
        "workspace",
        "packs",
        "templates",
        ".rail-local",
    }
    assert (store / ".gitignore").read_bytes() == b".rail-local/\n"


def test_init_refuses_an_existing_store_even_when_malformed(tmp_path):
    store = _init(tmp_path)
    before = (store / "rail.yaml").read_bytes()

    again = invoke("init", {"dir": str(tmp_path)}, actor="human")

    assert again["error"]["code"] == "CONFLICT"
    assert (store / "rail.yaml").read_bytes() == before

    broken = tmp_path / "broken"
    (broken / "context-rail").mkdir(parents=True)
    (broken / "context-rail" / "rail.yaml").write_bytes(b"schema: [")
    refused = invoke("init", {"dir": str(broken)}, actor="human")
    assert refused["error"]["code"] == "CONFLICT"
    assert (broken / "context-rail" / "rail.yaml").read_bytes() == b"schema: ["


def test_init_confines_root_and_validates_the_pack(tmp_path):
    escape = invoke(
        "init", {"dir": str(tmp_path / "project"), "root": "../escape"}, actor="human"
    )
    assert escape["error"]["code"] == "FORBIDDEN"

    unknown = invoke(
        "init", {"dir": str(tmp_path / "other"), "pack": "nosuch"}, actor="human"
    )
    assert unknown["error"]["code"] == "VALIDATION_FAILED"


def test_keyed_init_replays_instead_of_refusing(tmp_path):
    first = invoke("init", {"dir": str(tmp_path), "key": "k-init"}, actor="human")
    second = invoke("init", {"dir": str(tmp_path), "key": "k-init"}, actor="human")

    assert is_ok(first), first
    assert is_ok(second), second
    assert second["value"] == first["value"]


# ── add ───────────────────────────────────────────────────────────────────


def test_add_move_show_roundtrip(tmp_path):
    assert is_ok(invoke("init", {"dir": str(tmp_path)}, actor="human"))
    r = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=tmp_path / "context-rail", actor="cto"
    )
    slug = r["value"]["slug"]
    etag = invoke("show", {"slug": slug}, store=tmp_path / "context-rail")["value"]["doc"]["etag"]
    moved = invoke(
        "move",
        {"slug": slug, "status": "doing", "expect": etag},
        store=tmp_path / "context-rail",
        actor="cto",
    )
    assert is_ok(moved)
    bad = invoke(
        "move",
        {"slug": slug, "status": "done", "expect": etag},
        store=tmp_path / "context-rail",
        actor="cto",
    )
    assert bad["error"]["code"] == "CONFLICT"


def test_add_writes_pinned_fields_and_one_creation_record(tmp_path):
    store = _init(tmp_path)
    before = _files(store, "records")

    result = invoke(
        "add",
        {"preset": "task", "title": "Login page", "tag": ["ui"]},
        store=store,
        actor="cto",
    )

    assert is_ok(result), result
    assert result["value"]["slug"] == "login-page"
    assert result["value"]["path"] == "items/task/login-page.md"
    front, _ = _front(store, "items/task/login-page.md")
    assert front["primitive"] == "item"
    assert front["preset"] == "task"
    assert front["title"] == "Login page"
    assert front["status"] == "todo"
    assert front["tags"] == ["task", "ui"]
    assert front["created"] == front["updated"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", front["created"])

    payload = _new_record(store, before)
    assert payload["kind"] == "doc.created"
    assert payload["about"] == ["login-page"]
    assert payload["actor"] == "cto"
    assert payload["digest"] == record_digest(payload)

    record_doc = invoke("show", {"slug": payload["id"]}, store=store)["value"]
    assert record_doc["doc"]["digest"] == payload["digest"]
    assert record_doc["doc"]["fields"]["kind"] == "doc.created"
    assert record_doc["doc"]["fields"]["actor"] == "cto"
    assert record_doc["doc"]["fields"]["at"] == payload["at"]
    assert record_doc["doc"]["fields"]["data"] == {"path": "items/task/login-page.md"}
    assert record_doc["out"]["about"] == ["login-page"]


def test_body_file_sets_the_body_on_add_and_update(tmp_path):
    store = _init(tmp_path)
    body_file = tmp_path / "body.md"
    body_file.write_text("See [[auth]] and more.\n", encoding="utf-8", newline="\n")

    added = invoke(
        "add", {"preset": "note", "title": "Auth notes", "body_file": str(body_file)}, store=store
    )
    assert is_ok(added), added
    assert invoke("show", {"slug": "auth-notes"}, store=store)["value"]["body"] == (
        "See [[auth]] and more.\n"
    )

    body_file.write_text("# Replaced\n", encoding="utf-8", newline="\n")
    updated = invoke(
        "update",
        {
            "slug": "auth-notes",
            "expect": _etag(store, "auth-notes"),
            "body_file": str(body_file),
        },
        store=store,
    )
    assert is_ok(updated), updated
    assert invoke("show", {"slug": "auth-notes"}, store=store)["value"]["body"] == "# Replaced\n"

    missing = invoke(
        "add",
        {"preset": "task", "title": "X", "body_file": str(tmp_path / "nope.md")},
        store=store,
    )
    assert missing["error"]["code"] == "NOT_FOUND"


def test_add_makes_slug_collisions_unique(tmp_path):
    store = _init(tmp_path)
    slugs = []
    for _ in range(3):
        result = invoke("add", {"preset": "task", "title": "Login page"}, store=store)
        slugs.append(result["value"]["slug"])
        if len(slugs) == 1:
            first = (store / "items/task/login-page.md").read_bytes()
    assert slugs == ["login-page", "login-page-2", "login-page-3"]
    assert (store / "items/task/login-page.md").read_bytes() == first


def test_unknown_preset_is_refused_before_any_path(tmp_path):
    store = _init(tmp_path)
    before = _files(store)

    result = invoke("add", {"preset": "nosuch", "title": "X"}, store=store)

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert _files(store) == before


def test_add_refuses_set_values_the_add_arguments_own(tmp_path):
    store = _init(tmp_path)
    before = _files(store)

    for assignment in ("title=Other", "tags=[]"):
        result = invoke(
            "add", {"preset": "task", "title": "X", "set": [assignment]}, store=store
        )
        assert result["error"]["code"] == "VALIDATION_FAILED", assignment
    assert _files(store) == before


def test_add_with_relations_and_refusals(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    before = _files(store)

    linked = invoke(
        "add",
        {
            "preset": "task",
            "title": "Sign in",
            "rel": ["part_of=auth", "depends_on=login-page"],
        },
        store=store,
    )

    assert is_ok(linked), linked
    shown = invoke("show", {"slug": "sign-in"}, store=store)["value"]
    assert shown["out"] == {"part_of": ["auth"], "depends_on": ["login-page"]}

    unknown = invoke(
        "add", {"preset": "task", "title": "X1", "rel": ["foo=auth"]}, store=store
    )
    assert unknown["error"]["code"] == "VALIDATION_FAILED"
    missing = invoke(
        "add", {"preset": "task", "title": "X2", "rel": ["part_of=nosuch"]}, store=store
    )
    assert missing["error"]["code"] == "NOT_FOUND"
    malformed = invoke(
        "add", {"preset": "task", "title": "X3", "rel": ["part_of"]}, store=store
    )
    assert malformed["error"]["code"] == "VALIDATION_FAILED"

    after = _files(store)
    assert not (store / "items/task/x1.md").exists()
    assert not (store / "items/task/x2.md").exists()
    assert not (store / "items/task/x3.md").exists()
    assert {path for path in after - before if path.startswith("items/")} == {
        "items/task/sign-in.md"
    }


UNKNOWN_RELATIONS = [
    {"src": "sign-in", "rel": "foo", "dst": "login-page"},
    {"src": "sign-in", "rel": "depends_on", "dst": "nosuch"},
]


@pytest.mark.parametrize("link_args", UNKNOWN_RELATIONS)
def test_unknown_relation_refused(tmp_path, link_args):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    assert is_ok(invoke("add", {"preset": "task", "title": "Sign in"}, store=store))
    etag = _etag(store, "sign-in")
    before = (store / "items/task/sign-in.md").read_bytes()

    result = invoke(
        "link", {**link_args, "expect": etag}, store=store, actor="cto"
    )

    expected = "VALIDATION_FAILED" if link_args["rel"] == "foo" else "NOT_FOUND"
    assert result["error"]["code"] == expected
    assert (store / "items/task/sign-in.md").read_bytes() == before


# ── update ────────────────────────────────────────────────────────────────


def test_update_applies_typed_values_tags_and_keeps_created(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    created = invoke("show", {"slug": "login-page"}, store=store)["value"]["doc"]["created"]
    etag = _etag(store, "login-page")
    time.sleep(1.1)

    result = invoke(
        "update",
        {
            "slug": "login-page",
            "expect": etag,
            "set": ["estimate=3", "urgent=true"],
            "tag": ["+ui2", "-task"],
        },
        store=store,
        actor="cto",
    )

    assert is_ok(result), result
    doc = invoke("show", {"slug": "login-page"}, store=store)["value"]["doc"]
    assert doc["fields"]["estimate"] == 3
    assert doc["fields"]["urgent"] is True
    assert doc["tags"] == ["ui2"]
    assert doc["created"] == created
    assert doc["updated"] > doc["created"]
    assert result["value"]["record"]


@pytest.mark.parametrize(
    "assignment",
    [
        "status=doing",
        "primitive=note",
        "preset=task",
        "id=x",
        "digest=x",
        "created=x",
        "updated=x",
        "path=x",
        "slug=x",
        "nokey",
    ],
)
def test_update_refuses_forbidden_edits(tmp_path, assignment):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    etag = _etag(store, "login-page")
    before = (store / "items/task/login-page.md").read_bytes()

    result = invoke(
        "update",
        {"slug": "login-page", "expect": etag, "set": [assignment]},
        store=store,
    )

    assert result["error"]["code"] == "VALIDATION_FAILED", assignment
    assert (store / "items/task/login-page.md").read_bytes() == before


def test_update_cannot_change_status(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    etag = _etag(store, "login-page")

    result = invoke(
        "update",
        {"slug": "login-page", "expect": etag, "set": ["status=doing"]},
        store=store,
        actor="cto",
    )

    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_update_refuses_an_unchanged_edit(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    etag = _etag(store, "login-page")

    result = invoke("update", {"slug": "login-page", "expect": etag}, store=store)

    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_update_refuses_records(tmp_path):
    store = _init(tmp_path)
    decision = invoke("add", {"preset": "decision", "title": "Use SQLite"}, store=store)
    event = invoke("add", {"preset": "event", "title": "Deploy done"}, store=store)
    event_rel = next(
        path
        for path in _files(store, "records")
        if path.endswith(f"/{event['value']['record']}.json")
    )
    for slug, rel in (
        (decision["value"]["slug"], "records/decisions/use-sqlite.md"),
        (event["value"]["record"], event_rel),
    ):
        etag = _etag(store, slug)
        before = (store / rel).read_bytes()
        result = invoke(
            "update", {"slug": slug, "expect": etag, "set": ["x=1"]}, store=store
        )
        assert result["error"]["code"] == "INVALID_STATE"
        assert (store / rel).read_bytes() == before


# ── move ──────────────────────────────────────────────────────────────────


def test_invalid_transition_refused(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    etag = _etag(store, "login-page")
    before = (store / "items/task/login-page.md").read_bytes()

    result = invoke(
        "move", {"slug": "login-page", "status": "done", "expect": etag}, store=store, actor="cto"
    )

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert (store / "items/task/login-page.md").read_bytes() == before


def test_move_records_status_changed(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))

    before = _files(store, "records")
    first = invoke(
        "move",
        {"slug": "login-page", "status": "doing", "expect": _etag(store, "login-page")},
        store=store,
        actor="cto",
    )
    assert is_ok(first), first
    payload = _new_record(store, before)
    assert payload["kind"] == "status.changed"
    assert payload["about"] == ["login-page"]
    assert payload["data"] == {"from": "todo", "to": "doing", "resolution": None}

    before = _files(store, "records")
    second = invoke(
        "move",
        {
            "slug": "login-page",
            "status": "done",
            "expect": _etag(store, "login-page"),
            "resolution": "mitigated",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(second), second
    payload = _new_record(store, before)
    assert payload["data"] == {"from": "doing", "to": "done", "resolution": "mitigated"}


def test_move_checks_expect_before_lifecycle(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    stale = _etag(store, "login-page")
    assert is_ok(
        invoke(
            "update",
            {"slug": "login-page", "expect": stale, "tag": ["+ui"]},
            store=store,
            actor="cto",
        )
    )

    refused = invoke(
        "move", {"slug": "login-page", "status": "done", "expect": stale}, store=store
    )

    assert refused["error"]["code"] == "CONFLICT"

    assert is_ok(
        invoke(
            "move",
            {"slug": "login-page", "status": "doing", "expect": _etag(store, "login-page")},
            store=store,
            actor="cto",
        )
    )
    before = (store / "items/task/login-page.md").read_bytes()
    self_transition = invoke(
        "move",
        {"slug": "login-page", "status": "doing", "expect": _etag(store, "login-page")},
        store=store,
        actor="cto",
    )
    assert self_transition["error"]["code"] == "VALIDATION_FAILED"
    assert (store / "items/task/login-page.md").read_bytes() == before


# ── link ──────────────────────────────────────────────────────────────────


def test_link_adds_and_removes_relations(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))
    assert is_ok(invoke("add", {"preset": "task", "title": "Sign in"}, store=store))

    before = _files(store, "records")
    added = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": _etag(store, "sign-in"),
        },
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    shown = invoke("show", {"slug": "sign-in"}, store=store)["value"]
    assert shown["out"]["depends_on"] == ["login-page"]
    payload = _new_record(store, before)
    assert payload["kind"] == "relation.changed"
    assert payload["about"] == ["sign-in"]

    removed = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": _etag(store, "sign-in"),
            "remove": True,
        },
        store=store,
        actor="cto",
    )
    assert is_ok(removed), removed
    assert invoke("show", {"slug": "sign-in"}, store=store)["value"]["out"] == {}

    before_bytes = (store / "items/task/sign-in.md").read_bytes()
    stale = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": "0123456789abcdef",
        },
        store=store,
    )
    assert stale["error"]["code"] == "CONFLICT"
    assert (store / "items/task/sign-in.md").read_bytes() == before_bytes


# ── find ──────────────────────────────────────────────────────────────────


def test_find_filters_and_orders(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(
        invoke(
            "add", {"preset": "task", "title": "Task one", "rel": ["part_of=auth"]}, store=store
        )
    )
    assert is_ok(
        invoke("add", {"preset": "task", "title": "Task two", "tag": ["extra"]}, store=store)
    )
    touch_doc(store, "items/task/task-one.md", updated="2026-01-01T00:00:00Z")
    touch_doc(store, "items/task/task-two.md", updated="2026-03-01T00:00:00Z")

    tasks = invoke("find", {"preset": "task"}, store=store)
    assert is_ok(tasks), tasks
    assert set(tasks["value"]) == {"items", "total", "omitted"}
    assert [item["slug"] for item in tasks["value"]["items"]] == ["task-two", "task-one"]
    assert set(tasks["value"]["items"][0]) == {
        "slug",
        "title",
        "primitive",
        "preset",
        "status",
        "path",
    }

    tagged = invoke("find", {"tag": ["extra"]}, store=store)["value"]
    assert [item["slug"] for item in tagged["items"]] == ["task-two"]
    related = invoke("find", {"rel": "part_of=auth"}, store=store)["value"]
    assert [item["slug"] for item in related["items"]] == ["task-one"]
    category = invoke("find", {"category": "not_started"}, store=store)["value"]
    assert [item["slug"] for item in category["items"]] == ["auth", "task-two", "task-one"]
    text = invoke("find", {"text": "Task one"}, store=store)["value"]
    assert [item["slug"] for item in text["items"]] == ["task-one"]
    status = invoke("find", {"status": "todo", "preset": "task"}, store=store)["value"]
    assert [item["slug"] for item in status["items"]] == ["task-two", "task-one"]


def test_find_bounds_and_malformed_input(tmp_path):
    store = _init(tmp_path)
    for title in ("One", "Two", "Three"):
        assert is_ok(invoke("add", {"preset": "task", "title": title}, store=store))

    for bad in ({"limit": 0}, {"limit": 101}, {"text": '"unclosed'}, {"rel": "part_of"}):
        result = invoke("find", bad, store=store)
        assert result["error"]["code"] == "VALIDATION_FAILED", bad

    one = invoke("find", {"limit": 1}, store=store)["value"]
    assert len(one["items"]) == 1
    assert one["omitted"] == one["total"] - 1


# ── show ──────────────────────────────────────────────────────────────────


def test_show_relations_body_and_not_found(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(
        invoke(
            "add", {"preset": "task", "title": "Login page", "rel": ["part_of=auth"]}, store=store
        )
    )

    shown = invoke("show", {"slug": "login-page"}, store=store)
    assert is_ok(shown), shown
    doc = shown["value"]["doc"]
    assert doc["etag"] and doc["path"] == "items/task/login-page.md"
    assert doc["status"] == "todo"
    assert shown["value"]["out"] == {"part_of": ["auth"]}
    assert "part_of" not in shown["value"]["in"]
    assert shown["value"]["body_omitted"] == 0

    short = invoke("show", {"slug": "login-page", "body_chars": 10}, store=store)["value"]
    assert len(short["body"]) == 10
    assert short["body_omitted"] > 0

    too_many = invoke("show", {"slug": "login-page", "body_chars": 20001}, store=store)
    assert too_many["error"]["code"] == "VALIDATION_FAILED"

    missing = invoke("show", {"slug": "nosuch"}, store=store)
    assert missing["error"]["code"] == "NOT_FOUND"


def test_show_refuses_an_ambiguous_slug(store_with_duplicate):
    result = invoke("show", {"slug": "x"}, store=store_with_duplicate)

    assert result["error"]["code"] == "CONFLICT"
    assert result["error"]["details"]["paths"] == ["items/task/x.md", "notes/note/x.md"]


def test_show_maps_a_vanished_file_to_not_found(tmp_path, monkeypatch):
    from context_rail.gateway.handlers import views

    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Login page"}, store=store))

    def vanished(*_args, **_kwargs):
        raise FileNotFoundError("the indexed file is gone")

    monkeypatch.setattr(views.query, "show", vanished)

    result = invoke("show", {"slug": "login-page"}, store=store)

    assert result["error"]["code"] == "NOT_FOUND"


# ── status ────────────────────────────────────────────────────────────────


def test_status_view_lists_executable_and_blocked(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(
        invoke("add", {"preset": "task", "title": "t1", "rel": ["part_of=auth"]}, store=store)
    )
    assert is_ok(
        invoke(
            "add",
            {
                "preset": "task",
                "title": "t2",
                "rel": ["part_of=auth", "depends_on=t1"],
            },
            store=store,
        )
    )

    status = invoke("status", {}, store=store)

    assert is_ok(status), status
    assert status["value"]["counts"] == {
        "not_started": 3,
        "active": 0,
        "blocked": 0,
        "done": 0,
        "dropped": 0,
    }
    assert status["value"]["executable"] == ["t1"]
    assert status["value"]["blocked"] == {"t2": "depends_on:t1"}
    assert status["value"]["stuck"] == []
    assert status["value"]["omitted"] == {"executable": 0, "blocked": 0, "stuck": 0}

    assert is_ok(
        invoke("add", {"preset": "task", "title": "t3", "rel": ["part_of=auth"]}, store=store)
    )
    status = invoke("status", {}, store=store)["value"]
    assert status["counts"]["not_started"] == 4
    assert status["executable"] == ["t1", "t3"]
    shown = invoke("show", {"slug": "auth"}, store=store)["value"]
    assert shown["in"]["part_of"] == ["t1", "t2", "t3"]


def test_status_caps_each_list_at_twenty(tmp_path):
    store = _init(tmp_path)
    for index in range(1, 26):
        _write_doc(
            store, f"items/task/task-{index:02d}.md", _task_front(f"task-{index:02d}")
        )
    for index in range(1, 26):
        _write_doc(
            store,
            f"items/task/dep-{index:02d}.md",
            _task_front(f"dep-{index:02d}", depends_on="nosuch"),
        )
    assert is_ok(invoke("reindex", {}, store=store))

    status = invoke("status", {}, store=store)["value"]

    assert status["counts"]["not_started"] == 50
    assert status["executable"] == [f"task-{index:02d}" for index in range(1, 21)]
    assert status["omitted"]["executable"] == 5
    assert list(status["blocked"]) == [f"dep-{index:02d}" for index in range(1, 21)]
    assert set(status["blocked"].values()) == {"depends_on:nosuch"}
    assert status["omitted"]["blocked"] == 5
    assert status["stuck"] == []
    assert status["omitted"]["stuck"] == 0


def test_status_blocked_drops_finished_dependents_and_keeps_blocked_items(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "open"}, store=store))
    for title in ("waits", "scrapped", "still-waiting", "halted"):
        assert is_ok(
            invoke(
                "add",
                {"preset": "task", "title": title, "rel": ["depends_on=open"]},
                store=store,
            )
        )

    for status in ("doing", "done"):
        assert is_ok(
            invoke(
                "move",
                {"slug": "waits", "status": status, "expect": _etag(store, "waits")},
                store=store,
                actor="cto",
            )
        )
    assert is_ok(
        invoke(
            "move",
            {"slug": "scrapped", "status": "dropped", "expect": _etag(store, "scrapped")},
            store=store,
            actor="cto",
        )
    )
    assert is_ok(
        invoke(
            "move",
            {"slug": "halted", "status": "doing", "expect": _etag(store, "halted")},
            store=store,
            actor="cto",
        )
    )
    assert is_ok(
        invoke(
            "move",
            {"slug": "halted", "status": "blocked", "expect": _etag(store, "halted")},
            store=store,
            actor="cto",
        )
    )

    status = invoke("status", {}, store=store)["value"]

    assert status["blocked"] == {
        "halted": "status",
        "still-waiting": "depends_on:open",
    }
    assert status["counts"]["done"] == 1
    assert status["counts"]["dropped"] == 1
    assert status["omitted"]["blocked"] == 0


def test_status_blocked_omits_finished_items_before_capping(tmp_path):
    store = _init(tmp_path)
    _write_doc(store, "items/task/open.md", _task_front("open"))
    for index in range(1, 23):
        _write_doc(
            store,
            f"items/task/waiting-{index:02d}.md",
            _task_front(f"waiting-{index:02d}", depends_on="open"),
        )
    for finished in ("done", "dropped"):
        _write_doc(
            store,
            f"items/task/retired-{finished}.md",
            _task_front(f"retired-{finished}", status=finished, depends_on="open"),
        )
    _write_doc(
        store,
        "items/task/halted.md",
        _task_front("halted", status="blocked", depends_on="open"),
    )
    assert is_ok(invoke("reindex", {}, store=store))

    status = invoke("status", {}, store=store)["value"]

    expected = ["halted", *[f"waiting-{index:02d}" for index in range(1, 20)]]
    assert list(status["blocked"]) == expected
    assert status["blocked"]["halted"] == "status"
    assert not any(slug.startswith("retired-") for slug in status["blocked"])
    assert status["omitted"]["blocked"] == 3


def test_status_stuck_follows_factual_activity(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "goal", "title": "Auth"}, store=store))
    assert is_ok(
        invoke("add", {"preset": "task", "title": "t1", "rel": ["part_of=auth"]}, store=store)
    )
    assert is_ok(
        invoke(
            "move",
            {"slug": "t1", "status": "doing", "expect": _etag(store, "t1")},
            store=store,
            actor="cto",
        )
    )
    status = invoke("status", {}, store=store)["value"]
    assert status["counts"]["active"] == 1
    assert status["stuck"] == []

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_doc(
        store,
        "items/task/t2.md",
        _task_front("t2", status="doing", part_of="auth", created=now, updated=now),
    )
    status = invoke("status", {}, store=store)["value"]
    assert status["counts"]["active"] == 2
    assert "t2" in status["stuck"]
    assert "t1" not in status["stuck"]


# ── reindex ───────────────────────────────────────────────────────────────


def test_reindex_reports_sync_stats(tmp_path):
    store = _init(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "One"}, store=store))
    assert is_ok(invoke("add", {"preset": "task", "title": "Two"}, store=store))

    first = invoke("reindex", {}, store=store)["value"]
    assert first == {"added": 0, "changed": 0, "removed": 0, "skipped": 0}
    assert invoke("reindex", {}, store=store)["value"] == first

    touch_doc(store, "items/task/one.md", title="Renamed by hand")
    changed = invoke("reindex", {}, store=store)["value"]
    assert changed["changed"] == 1
    assert invoke("show", {"slug": "one"}, store=store)["value"]["doc"]["title"] == "Renamed by hand"

    full = invoke("reindex", {"full": True}, store=store)["value"]
    docs = len(list(store.rglob("*.md"))) + len(list(store.rglob("*.json")))
    assert full["added"] == docs


# ── gateway contract ──────────────────────────────────────────────────────


def test_unknown_tool_param_and_type_are_refused(tmp_path):
    store = _init(tmp_path)
    assert invoke("nosuch", {}, store=store)["error"]["code"] == "VALIDATION_FAILED"
    assert (
        invoke("show", {"slug": "one", "nope": 1}, store=store)["error"]["code"]
        == "VALIDATION_FAILED"
    )
    assert invoke("show", {"slug": 5}, store=store)["error"]["code"] == "VALIDATION_FAILED"
    assert invoke("find", {"limit": True}, store=store)["error"]["code"] == "VALIDATION_FAILED"


def test_missing_store_is_not_found(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    result = invoke("status", {})

    assert result["error"]["code"] == "NOT_FOUND"
    assert result["error"]["message"] == "no store found"


def test_permission_refusal_is_reported(tmp_path, monkeypatch):
    from context_rail.gateway import gateway as gateway_module

    store = _init(tmp_path)
    monkeypatch.setattr(gateway_module.rights, "allowed", lambda *_args: "not allowed")

    result = invoke("status", {}, store=store)

    assert result["error"]["code"] == "PERMISSION_DENIED"


def test_unexpected_fault_is_internal(tmp_path, monkeypatch):
    from context_rail.gateway.handlers import views

    store = _init(tmp_path)

    def explode(_ctx, _args):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "status", explode)

    result = invoke("status", {}, store=store)

    assert result["error"]["code"] == "INTERNAL"


def test_actor_precedence(tmp_path, monkeypatch):
    store = _init(tmp_path)

    def actor_of(result: dict[str, Any]) -> str:
        record = result["value"]["record"]
        matches = [path for path in (store / "records").rglob(f"{record}.json")]
        assert len(matches) == 1, matches
        return _record_payload(
            store, matches[0].relative_to(store).as_posix()
        )["actor"]

    default = invoke("add", {"preset": "task", "title": "A"}, store=store)
    monkeypatch.setenv("RAIL_ACTOR", "w9")
    from_env = invoke("add", {"preset": "task", "title": "B"}, store=store)
    explicit = invoke("add", {"preset": "task", "title": "C"}, store=store, actor="cto")

    assert actor_of(default) == "human"
    assert actor_of(from_env) == "w9"
    assert actor_of(explicit) == "cto"


# ── records through add ───────────────────────────────────────────────────


def test_add_decision_writes_one_markdown_record(tmp_path):
    store = _init(tmp_path)
    before = _files(store, "records")

    result = invoke(
        "add", {"preset": "decision", "title": "Use SQLite"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    new = _files(store, "records") - before
    assert new == {"records/decisions/use-sqlite.md"}
    front, body = _front(store, "records/decisions/use-sqlite.md")
    assert front["primitive"] == "record"
    assert front["preset"] == "decision"
    assert front["title"] == "Use SQLite"
    assert front["actor"] == "cto"
    metadata = {
        key: front[key]
        for key in ("id", "primitive", "preset", "kind", "at", "actor", "about", "data")
    }
    assert front["digest"] == record_digest({**metadata, "body": body})
    assert result["value"]["slug"] == "use-sqlite"
    assert result["value"]["record"] == front["id"]

    shown = invoke("show", {"slug": "use-sqlite"}, store=store)["value"]
    assert shown["doc"]["primitive"] == "record"
    assert shown["doc"]["digest"] == front["digest"]
    found = invoke("find", {"text": "SQLite"}, store=store)["value"]
    assert [item["slug"] for item in found["items"]] == ["use-sqlite"]


def test_add_event_writes_one_json_record(tmp_path):
    store = _init(tmp_path)
    before = _files(store, "records")

    result = invoke(
        "add", {"preset": "event", "title": "Deploy done"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    new = sorted(_files(store, "records") - before)
    assert len(new) == 1
    rel = new[0]
    assert re.fullmatch(r"records/\d{4}-\d{2}/[0-9A-Z]{26}\.json", rel), rel
    payload = _record_payload(store, rel)
    assert set(payload) == RECORD_KEYS
    assert payload["primitive"] == "record"
    assert payload["preset"] == "event"
    assert payload["actor"] == "cto"
    assert payload["about"] == []
    assert payload["digest"] == record_digest(payload)
    assert payload["id"] == rel.rsplit("/", 1)[1][: -len(".json")]
    assert result["value"]["record"] == payload["id"]


def test_add_event_refuses_markdown_only_inputs(tmp_path):
    store = _init(tmp_path)
    before = _files(store)

    tagged = invoke(
        "add", {"preset": "event", "title": "Deploy done", "tag": ["ops"]}, store=store
    )
    body = invoke(
        "add", {"preset": "event", "title": "Deploy done", "body_file": __file__}, store=store
    )

    assert tagged["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert _files(store) == before


def test_keyed_decision_replay_adds_no_second_record(tmp_path):
    store = _init(tmp_path)

    first = invoke(
        "add", {"preset": "decision", "title": "Use SQLite", "key": "k-dec"}, store=store
    )
    second = invoke(
        "add", {"preset": "decision", "title": "Use SQLite", "key": "k-dec"}, store=store
    )
    third = invoke(
        "add", {"preset": "decision", "title": "Use Postgres", "key": "k-dec"}, store=store
    )

    slugs = [result["value"]["slug"] for result in (first, second, third)]
    assert slugs == ["use-sqlite", "use-sqlite", "use-sqlite"]
    assert _files(store, "records/decisions") == {"records/decisions/use-sqlite.md"}


def test_keyed_add_replays_the_original_result(tmp_path):
    store = _init(tmp_path)

    first = invoke(
        "add", {"preset": "task", "title": "Retry me", "key": "k-retry"}, store=store
    )
    after_first = _files(store, "records")
    second = invoke(
        "add", {"preset": "task", "title": "Retry me", "key": "k-retry"}, store=store
    )
    third = invoke(
        "add", {"preset": "task", "title": "Retry changed", "key": "k-retry"}, store=store
    )

    assert [result["value"]["slug"] for result in (first, second, third)] == [
        "retry-me",
        "retry-me",
        "retry-me",
    ]
    assert _files(store, "records") == after_first
    found = invoke("find", {"text": "Retry"}, store=store)["value"]
    assert [item["slug"] for item in found["items"]] == ["retry-me"]
