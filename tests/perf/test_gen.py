"""The 200-doc generator behind every index, query and budget test."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from context_rail.model.pack import load_pack
from context_rail.store.doc import parse_doc
from context_rail.store.index import Index, SyncStats
from perf.gen import make_store


def _documents(store: Path) -> list[str]:
    return sorted(
        path.relative_to(store).as_posix()
        for path in store.rglob("*")
        if path.is_file()
    )


def _parts(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :])


def _frontmatter(store: Path, rel: str) -> dict:
    front, _ = _parts((store / rel).read_text(encoding="utf-8"))
    return yaml.safe_load(front)


def test_make_store_lays_out_the_documented_counts(tmp_path):
    store = make_store(tmp_path, 200)
    assert store == tmp_path / "context-rail"
    expected = (
        ["rail.yaml"]
        + [f"project/g{i}.md" for i in range(20)]
        + [f"notes/note/n{i}.md" for i in range(40)]
        + [f"items/task/t{i}.md" for i in range(140)]
    )
    assert _documents(store) == sorted(expected)


def test_task_relations_follow_the_contract(tmp_path):
    store = make_store(tmp_path, 200)
    for i in (0, 5, 10, 139):
        front = _frontmatter(store, f"items/task/t{i}.md")
        assert front["part_of"] == [f"[[g{i % 20}]]"]
        if i > 0 and i % 5 == 0:
            assert front["depends_on"] == [f"[[t{i - 1}]]"]
        else:
            assert "depends_on" not in front


def test_statuses_cycle_and_notes_have_none(tmp_path):
    store = make_store(tmp_path, 200)
    for i in range(6):
        assert _frontmatter(store, f"project/g{i}.md")["status"] == "doing"
        assert _frontmatter(store, f"items/task/t{i}.md")["status"] == (
            "todo",
            "doing",
            "done",
        )[i % 3]
    assert "status" not in _frontmatter(store, "notes/note/n0.md")


def test_make_store_is_deterministic_and_a_seed_changes_only_bodies(tmp_path):
    first = make_store(tmp_path / "a", 20, seed=0)
    same = make_store(tmp_path / "b", 20, seed=0)
    other = make_store(tmp_path / "c", 20, seed=1)
    assert _documents(first) == _documents(same)
    for rel in _documents(first):
        assert (first / rel).read_bytes() == (same / rel).read_bytes()
        if rel == "rail.yaml":
            assert (first / rel).read_bytes() == (other / rel).read_bytes()
            continue
        front_first, body_first = _parts((first / rel).read_text(encoding="utf-8"))
        front_other, body_other = _parts((other / rel).read_text(encoding="utf-8"))
        assert front_first == front_other
        assert body_first != body_other
        title = yaml.safe_load(front_first)["title"]
        assert title in body_first
        assert len(body_first) >= 101


def test_generated_docs_parse_cleanly_and_index_without_skips(tmp_path):
    store = make_store(tmp_path, 200)
    for rel in _documents(store):
        if rel == "rail.yaml":
            continue
        assert parse_doc(rel, (store / rel).read_bytes()).problems == ()
    index = Index.open(store, load_pack("basic", store))
    try:
        assert index.sync() == SyncStats(200, 0, 0, 0)
    finally:
        index.close()


def test_make_store_rejects_a_negative_count(tmp_path):
    with pytest.raises(ValueError):
        make_store(tmp_path, -1)


def test_store_factory_returns_fresh_roots_with_only_the_manifest(store_factory):
    first = store_factory(docs=0)
    second = store_factory(docs=0)
    assert first != second
    assert first.parent != second.parent
    assert _documents(first) == ["rail.yaml"]
