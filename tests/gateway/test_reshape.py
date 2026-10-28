"""Rename and relocate: reshaping the store without breaking links."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from context_rail.core.result import is_ok
from context_rail.core.util import load_yaml
from context_rail.gateway.gateway import invoke
from helpers import run_cli, touch_doc

STAMP = "2026-10-28T00:00:00Z"


def _set_placement(store: Path, **placement: str) -> None:
    path = store / "rail.yaml"
    manifest = load_yaml(path.read_text(encoding="utf-8"))
    manifest["placement"].update(placement)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _snapshot(store: Path) -> dict[str, bytes]:
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file()
        and not path.relative_to(store).as_posix().startswith(".rail-local/")
    }


def _records(store: Path) -> dict[str, bytes]:
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in (store / "records").rglob("*")
        if path.is_file()
    }


def _record_payloads(store: Path) -> list[dict[str, object]]:
    payloads = []
    for path in sorted((store / "records").rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _frontmatter(store: Path, rel: str) -> dict[str, object]:
    text = (store / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
    _, rest = text.split("---\n", 1)
    raw, _body = rest.split("\n---\n", 1)
    parsed = load_yaml(raw)
    assert isinstance(parsed, dict)
    return parsed


# ── rename ────────────────────────────────────────────────────────────────


def test_rename_rewrites_links_everywhere(store_with_links: Path) -> None:
    store = store_with_links

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    assert result["value"]["rewritten"] == 3
    shown = invoke("show", {"slug": "authentication"}, store=store)
    assert is_ok(shown), shown
    assert shown["value"]["in"]["part_of"] == ["t1", "t2", "t3"]
    assert not (store / "items/goal/auth.md").exists()
    assert (store / "items/goal/authentication.md").is_file()


def test_rename_rewrites_body_only_links(tmp_path: Path, store_with_links: Path) -> None:
    store = store_with_links
    body_file = tmp_path / "note.md"
    body_file.write_text("Read [[auth|the goal]] first.\n", encoding="utf-8")
    added = invoke(
        "add",
        {"preset": "note", "title": "Pointers", "body_file": str(body_file)},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    assert result["value"]["rewritten"] == 4
    text = (store / "notes/note/pointers.md").read_text(encoding="utf-8")
    assert "[[authentication|the goal]]" in text
    assert "[[auth]]" not in text
    assert "[[auth|" not in text
    assert "part_of" not in _frontmatter(store, "notes/note/pointers.md")


def test_rename_to_existing_slug_refused(store_with_links: Path) -> None:
    store = store_with_links
    before = _snapshot(store)

    result = invoke("rename", {"old": "auth", "new": "t1"}, store=store, actor="cto")

    assert result["ok"] is False
    assert result["error"]["code"] == "CONFLICT"
    assert _snapshot(store) == before


def test_rename_never_rewrites_records(tmp_path: Path, store_with_links: Path) -> None:
    store = store_with_links
    body_file = tmp_path / "decision.md"
    body_file.write_text("Decided next to [[auth]].\n", encoding="utf-8")
    decided = invoke(
        "add",
        {"preset": "decision", "title": "Rename policy", "body_file": str(body_file)},
        store=store,
        actor="cto",
    )
    assert is_ok(decided), decided
    records = _records(store)

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    after = _records(store)
    for rel, data in records.items():
        assert after[rel] == data, rel
    renamed = [
        payload
        for payload in _record_payloads(store)
        if payload.get("kind") == "doc.renamed"
    ]
    assert len(renamed) == 1
    assert renamed[0]["about"] == ["authentication"]
    assert renamed[0]["data"] == {
        "from": "auth",
        "to": "authentication",
        "rewritten": 3,
    }


def test_rename_refuses_non_canonical_slug(store_with_links: Path) -> None:
    store = store_with_links
    before = _snapshot(store)

    for unusable in ("../x", "Authentication", "with space", ""):
        result = invoke(
            "rename", {"old": "auth", "new": unusable}, store=store, actor="cto"
        )
        assert result["ok"] is False, unusable
        assert result["error"]["code"] == "VALIDATION_FAILED", unusable
    assert _snapshot(store) == before


def test_rename_refuses_a_record(store_with_links: Path) -> None:
    store = store_with_links
    added = invoke(
        "add", {"preset": "decision", "title": "Use sqlite"}, store=store, actor="cto"
    )
    assert is_ok(added), added

    result = invoke(
        "rename", {"old": "use-sqlite", "new": "use-sqlite-3"}, store=store, actor="cto"
    )

    assert result["error"]["code"] == "INVALID_STATE"
    assert (store / "records/decisions/use-sqlite.md").is_file()


def test_rename_unknown_slug_is_not_found(store_with_links: Path) -> None:
    result = invoke(
        "rename", {"old": "missing", "new": "other"}, store=store_with_links, actor="cto"
    )

    assert result["error"]["code"] == "NOT_FOUND"


def test_rename_stamps_updated_and_keeps_created(store_with_links: Path) -> None:
    store = store_with_links
    touch_doc(store, "items/goal/auth.md", updated="2026-01-01T00:00:00Z")
    before = _frontmatter(store, "items/goal/auth.md")

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    after = _frontmatter(store, "items/goal/authentication.md")
    assert after["created"] == before["created"]
    assert after["updated"] != before["updated"]
    assert (after["primitive"], after["preset"]) == ("item", "goal")
    assert after["title"] == "auth"


def test_rename_preserves_untouched_frontmatter_lines(store_with_links: Path) -> None:
    store = store_with_links
    task = store / "items/task/t1.md"
    text = task.read_text(encoding="utf-8")
    task.write_text(
        text.replace("updated:", "# keep this comment\nupdated:", 1),
        encoding="utf-8",
        newline="\n",
    )

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    rewritten = task.read_text(encoding="utf-8")
    assert "# keep this comment" in rewritten
    assert "[[authentication]]" in rewritten


def test_rename_falls_back_for_flow_style_frontmatter(store_with_links: Path) -> None:
    store = store_with_links
    flow = store / "notes/note/flow.md"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(
        "---\n"
        "primitive: note\n"
        "preset: note\n"
        "title: Flow\n"
        "tags: []\n"
        "part_of: ['[[auth]]']\n"
        f"created: '{STAMP}'\n"
        f"updated: '{STAMP}'\n"
        "---\n"
        "# Flow\n\nSee [[auth]] for context.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(result), result
    assert result["value"]["rewritten"] == 4
    text = flow.read_text(encoding="utf-8")
    assert "[[authentication]]" in text
    assert "[[auth]]" not in text
    shown = invoke("show", {"slug": "flow"}, store=store)
    assert is_ok(shown), shown
    assert shown["value"]["out"]["part_of"] == ["authentication"]


def test_rename_is_idempotent_with_a_key(store_with_links: Path) -> None:
    store = store_with_links
    args = {"old": "auth", "new": "authentication", "key": "r1"}

    first = invoke("rename", args, store=store, actor="cto")
    second = invoke("rename", args, store=store, actor="cto")

    assert is_ok(first) and is_ok(second), (first, second)
    assert first["value"] == second["value"]
    assert first["value"]["rewritten"] == 3
    renamed = [
        payload
        for payload in _record_payloads(store)
        if payload.get("kind") == "doc.renamed"
    ]
    assert len(renamed) == 1


# ── relocate ──────────────────────────────────────────────────────────────


def test_relocate_after_placement_change(store_factory) -> None:
    store = store_factory(docs=20)
    _set_placement(store, item="work/{preset}/{slug}.md")

    result = invoke("relocate", {}, store=store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["moves"]
    shown = invoke("show", {"slug": "t0"}, store=store)
    assert is_ok(shown), shown
    assert shown["value"]["doc"]["path"] == "work/task/t0.md"
    found = invoke("find", {"preset": "task"}, store=store)
    assert is_ok(found), found
    assert found["value"]["items"]
    assert all(item["path"].startswith("work/task/") for item in found["value"]["items"])


def test_relocate_returns_sorted_moves(store_factory) -> None:
    store = store_factory(docs=20)
    _set_placement(store, item="work/{preset}/{slug}.md")

    result = invoke("relocate", {}, store=store, actor="cto")

    moves = result["value"]["moves"]
    assert moves == sorted(moves, key=lambda move: move["from"])
    assert all(set(move) == {"from", "to"} for move in moves)
    assert all(move["to"] == move["from"].replace("items/", "work/") for move in moves)


def test_relocate_dry_run_changes_nothing(store_factory) -> None:
    store = store_factory(docs=20)
    _set_placement(store, item="work/{preset}/{slug}.md")
    before = _snapshot(store)

    result = invoke("relocate", {"dry_run": True}, store=store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["dry_run"] is True
    assert result["value"]["moves"]
    assert _snapshot(store) == before


def test_relocate_keeps_records_where_they_are(store_with_links: Path) -> None:
    store = store_with_links
    records = _records(store)
    _set_placement(store, item="work/{preset}/{slug}.md")

    result = invoke("relocate", {}, store=store, actor="cto")

    assert is_ok(result), result
    after = _records(store)
    for rel, data in records.items():
        assert after[rel] == data, rel
    shown = invoke("show", {"slug": "t1"}, store=store)
    assert is_ok(shown), shown
    assert shown["value"]["doc"]["path"] == "work/task/t1.md"
    assert shown["value"]["out"]["part_of"] == ["auth"]


def test_relocate_refuses_an_undeclared_preset(store_factory) -> None:
    store = store_factory()
    mystery = store / "items/mystery/m1.md"
    mystery.parent.mkdir(parents=True)
    mystery.write_text(
        "---\n"
        "primitive: item\n"
        "preset: mystery\n"
        "title: Mystery\n"
        "status: todo\n"
        "tags: []\n"
        f"created: '{STAMP}'\n"
        f"updated: '{STAMP}'\n"
        "---\n# Mystery\n",
        encoding="utf-8",
        newline="\n",
    )

    result = invoke("relocate", {}, store=store, actor="cto")

    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert mystery.is_file()


def test_relocate_swaps_overlapping_paths(store_with_duplicate: Path) -> None:
    store = store_with_duplicate
    decided = invoke(
        "add", {"preset": "decision", "title": "Move policy", "rel": ["about=t0"]},
        store=store,
        actor="cto",
    )
    assert is_ok(decided), decided
    _set_placement(
        store,
        item="notes/note/{slug}.md",
        note="items/task/{slug}.md",
    )
    task_bytes = (store / "items/task/x.md").read_bytes()
    note_bytes = (store / "notes/note/x.md").read_bytes()
    records = _records(store)
    before = _snapshot(store)

    first = invoke("relocate", {"key": "swap"}, store=store, actor="cto")

    assert is_ok(first), first
    moves = first["value"]["moves"]
    assert moves == sorted(moves, key=lambda move: move["from"])
    assert {"from": "items/task/x.md", "to": "notes/note/x.md"} in moves
    assert {"from": "notes/note/x.md", "to": "items/task/x.md"} in moves
    after = _snapshot(store)
    assert len([rel for rel in after if rel.endswith(".md")]) == len(
        [rel for rel in before if rel.endswith(".md")]
    )
    destinations = {move["to"] for move in moves}
    for move in moves:
        assert move["to"] in after
        if move["from"] not in destinations:
            assert move["from"] not in after
    new_records = sorted(
        rel for rel in set(after) - set(before) if rel.startswith("records/")
    )
    assert len(new_records) == 1
    for rel, data in records.items():
        assert after[rel] == data, rel
    assert (store / "notes/note/x.md").read_bytes() == task_bytes
    assert (store / "items/task/x.md").read_bytes() == note_bytes

    second = invoke("relocate", {"key": "swap"}, store=store, actor="cto")

    assert is_ok(second), second
    assert second["value"]["moves"] == moves
    assert _snapshot(store) == after
    relocated = [
        payload
        for payload in _record_payloads(store)
        if payload.get("kind") == "doc.relocated"
    ]
    assert len(relocated) == 1


def test_relocate_without_moves_is_ok(store_with_links: Path) -> None:
    result = invoke("relocate", {}, store=store_with_links, actor="cto")

    assert is_ok(result), result
    assert result["value"]["moves"] == []
    assert result["value"]["dry_run"] is False


def test_relocate_is_idempotent_with_a_key(store_factory) -> None:
    store = store_factory(docs=20)
    _set_placement(store, item="work/{preset}/{slug}.md")

    first = invoke("relocate", {"key": "m1"}, store=store, actor="cto")
    second = invoke("relocate", {"key": "m1"}, store=store, actor="cto")

    assert is_ok(first) and is_ok(second), (first, second)
    assert first["value"]["moves"] == second["value"]["moves"]
    relocated = [
        payload
        for payload in _record_payloads(store)
        if payload.get("kind") == "doc.relocated"
    ]
    assert len(relocated) == 1


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_rename_and_relocate_dry_run(store_with_links: Path) -> None:
    store = store_with_links

    renamed = run_cli(store.parent, "rename", "auth", "authentication", store=store)

    assert renamed.returncode == 0, renamed.stderr
    payload = json.loads(renamed.stdout)
    assert payload["ok"] is True
    assert payload["value"]["rewritten"] == 3

    _set_placement(store, item="work/{preset}/{slug}.md")
    dry = run_cli(store.parent, "relocate", "--dry-run", store=store)

    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["value"]["moves"]
    assert (store / "items/task/t1.md").is_file()
