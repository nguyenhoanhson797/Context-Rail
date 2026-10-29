"""The adapter namespace: resolution, refusals, one commit and crash repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.model.manifest import load_manifest
from context_rail.store.adapters import (
    ADAPTER_PREFIX,
    AGENTS_PATH,
    END_MARK,
    START_MARK,
    read_managed,
    resolve_adapter,
)
from context_rail.store.location import PathEscape
from context_rail.store.write import Op, WriteRefused, commit, repair

AGENTS = "AGENTS.md"
SKILL = ".agents/skills/test-first/SKILL.md"


@pytest.mark.parametrize(
    "rel",
    [
        "@adapter/../escape.md",
        "@adapter/items/../../escape.md",
        "@adapter/C:/x.md",
        "@adapter/C:x.md",
        "@adapter//server/share/AGENTS.md",
        "@adapter/.agents/skills/bad\x00/SKILL.md",
        "@adapter/.agents/skills/CON/SKILL.md",
        "@adapter/.agents/skills/../x/SKILL.md",
        "@adapter/other.md",
        "@adapter/.agents/skills/test-first/other.md",
        "@adapter/.agents/skills/test-first/",
        "AGENTS.md",
        "",
    ],
)
def test_adapter_traversal_refused(tmp_path: Path, rel: str) -> None:
    with pytest.raises(PathEscape):
        resolve_adapter(tmp_path, rel)


def test_only_the_declared_targets_resolve(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    assert resolve_adapter(root, AGENTS_PATH) == root / "AGENTS.md"
    assert (
        resolve_adapter(root, f"{ADAPTER_PREFIX}{SKILL}")
        == root / ".agents" / "skills" / "test-first" / "SKILL.md"
    )


def test_commit_writes_the_adapter_target_and_one_record(store_factory) -> None:
    store = store_factory(docs=1)
    manifest = load_manifest(store)
    result = commit(
        store,
        manifest,
        [Op(AGENTS_PATH, b"hello\n", "absent")],
        {"kind": "adapters.synced", "data": {"schema": 1}},
        actor="cto",
    )
    assert (store.parent / AGENTS).read_bytes() == b"hello\n"
    assert result["written"] == [AGENTS]
    assert result["record"] is not None


def test_an_undeclared_adapter_target_is_refused(store_factory) -> None:
    store = store_factory(docs=1)
    manifest = load_manifest(store)
    with pytest.raises(WriteRefused) as refused:
        commit(
            store,
            manifest,
            [Op("@adapter/evil.md", b"evil\n", "absent")],
            None,
            actor="cto",
        )
    assert refused.value.code == "FORBIDDEN"
    assert not (store.parent / "evil.md").exists()


def test_store_paths_stay_confined_after_the_extension(store_factory) -> None:
    store = store_factory(docs=1)
    manifest = load_manifest(store)
    with pytest.raises(WriteRefused) as refused:
        commit(store, manifest, [Op("../x.md", b"x\n", "absent")], None, actor="cto")
    assert refused.value.code == "FORBIDDEN"
    assert not (store.parent / "x.md").exists()


def test_adapter_crash_repair_rolls_forward(skill_store: Path, crash_before_replace) -> None:
    repaired = repair(skill_store)
    assert repaired["unrecoverable"] == [], repaired
    assert repaired["completed"], repaired
    assert (skill_store.parent / AGENTS).is_file()
    assert (skill_store.parent / SKILL).is_file()
    text = (skill_store.parent / AGENTS).read_text(encoding="utf-8")
    assert text.count(START_MARK) == 1 and text.count(END_MARK) == 1
    assert read_managed(text) is not None
    assert sorted((skill_store / "records").rglob("*.json"))
