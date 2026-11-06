"""The compiled project cache: one ProjectModel per digest, pure and safe."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest

from context_rail.model.cache import cached_model, cached_project, clear_project_cache
from context_rail.model.compiler import compile_project, semantic_digest
from context_rail.model.pack import load_pack
from context_rail.model.presentation import default_presentation, presentation_hash
from helpers import write_pack

_PLAIN: dict[str, Any] = {
    "name": "plain",
    "version": 2,
    "statuses": {"todo": "not_started", "doing": "active", "done": "done", "dropped": "dropped"},
    "initial": "todo",
    "transitions": {"todo": ["doing", "dropped"], "doing": ["done", "dropped"], "done": ["doing"], "dropped": ["todo"]},
    "presets": {
        "goal": {"primitive": "item", "tags": ["goal"], "container": True},
        "task": {"primitive": "item", "tags": ["task"], "container": False},
        "note": {"primitive": "note", "tags": []},
        "decision": {"primitive": "record", "tags": ["decision"]},
        "event": {"primitive": "record", "tags": ["event"]},
        "check": {"primitive": "check", "tags": []},
        "charter": {"primitive": "policy", "tags": []},
        "vision": {"primitive": "note", "tags": []},
        "wont": {"primitive": "note", "tags": []},
        "skill": {"primitive": "note", "tags": []},
        "cto": {"primitive": "actor", "tags": []},
        "profile": {"primitive": "actor", "tags": []},
        "scope": {"primitive": "item", "tags": ["scope"], "container": True},
    },
    "policy": {"stuck_hours": 24, "retries": 1},
}


def _plain_pack(tmp_path: Path) -> Any:
    write_pack(tmp_path, "plain", copy.deepcopy(_PLAIN))
    return load_pack("plain", tmp_path)


def test_a_same_size_edit_under_the_same_timestamp_is_not_served_stale(tmp_path: Path) -> None:
    source = tmp_path / "rail.yaml"
    source.write_bytes(b"aaaa\n")
    first = source.stat()
    assert cached_model(source, source.read_bytes) == b"aaaa\n"
    source.write_bytes(b"bbbb\n")
    os.utime(source, ns=(first.st_atime_ns, first.st_mtime_ns))
    second = source.stat()
    assert (second.st_size, second.st_mtime_ns) == (first.st_size, first.st_mtime_ns)
    assert cached_model(source, source.read_bytes) == b"bbbb\n"


def test_a_packaged_source_is_never_read_just_to_key_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pack.yaml"
    source.write_bytes(b"packaged\n")
    reads: list[Path] = []
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (reads.append(self), original(self))[1])
    cached_model(source, lambda: "built", packaged=True)
    cached_model(source, lambda: "built", packaged=True)
    assert reads == []


def test_an_unchanged_source_is_parsed_once(tmp_path: Path) -> None:
    source = tmp_path / "pack.yaml"
    source.write_text("same\n", encoding="utf-8")
    parses: list[int] = []

    def build() -> str:
        parses.append(1)
        return source.read_text(encoding="utf-8")

    cached_model(source, build)
    cached_model(source, build)
    assert len(parses) == 1


def test_cached_project_reuses_identical_model(tmp_path: Path) -> None:
    clear_project_cache()
    pack = _plain_pack(tmp_path)
    presentation = default_presentation(pack)
    first = cached_project(pack, presentation)
    second = cached_project(pack, presentation)
    assert first is second
    assert first.full_digest == compile_project(pack, pack.operating, presentation).full_digest
    assert first.semantic_digest == semantic_digest(pack)


def test_cache_invalidates_on_presentation_change(tmp_path: Path) -> None:
    clear_project_cache()
    pack = _plain_pack(tmp_path)
    presentation = default_presentation(pack)
    first = cached_project(pack, presentation)
    changed = copy.deepcopy(presentation)
    from context_rail.model.presentation import Label
    changed.labels["task"] = Label(one="Job", many="Jobs")
    second = cached_project(pack, changed)
    assert second is not first
    assert second.presentation_digest != first.presentation_digest
    assert second.semantic_digest == first.semantic_digest


def test_cache_invalidates_on_semantic_change(tmp_path: Path) -> None:
    clear_project_cache()
    pack = _plain_pack(tmp_path)
    presentation = default_presentation(pack)
    first = cached_project(pack, presentation)
    mutated = copy.deepcopy(_PLAIN)
    mutated["statuses"]["todo"] = "active"
    write_pack(tmp_path, "plain", mutated)
    pack2 = load_pack("plain", tmp_path)
    second = cached_project(pack2, default_presentation(pack2))
    assert second is not first
    assert second.semantic_digest != first.semantic_digest
