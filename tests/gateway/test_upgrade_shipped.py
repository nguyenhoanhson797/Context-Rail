"""``rail ops upgrade`` brings shipped profiles and skills up to date and keeps edits."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import records_of, tree_bytes

import context_rail.gateway.handlers.upgrade as upgrade_handler
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.write import WriteRefused

LEGACY = (
    Path(__file__).parents[1] / "fixtures" / "legacy_profiles" / "basic" / "workspace"
)
PHRASE = "UPGRADE store"
CTO = "workspace/cto/profile.md"
IMPLEMENTER = "workspace/profiles/implementer/profile.md"
REVIEWER = "workspace/profiles/reviewer/profile.md"
PROFILES = (CTO, IMPLEMENTER, REVIEWER)
COORDINATE = "workspace/cto/skills/rail-coordinate.md"
RETIRED = (
    "workspace/cto/skills/test-first.md",
    "workspace/profiles/implementer/skills/test-first.md",
)


def _upgrade(store: Path, **args: Any) -> dict[str, Any]:
    return invoke("upgrade", args, store=store, actor="human")


def _legacy(git_approved_store: SimpleNamespace) -> Path:
    """A store whose workspace is exactly what the old basic pack wrote."""
    store = git_approved_store.store
    shutil.rmtree(store / "workspace")
    shutil.copytree(LEGACY, store / "workspace")
    return store


def test_upgrade_replaces_unedited_shipped_copies_and_seeds_new_skills(
    git_approved_store: SimpleNamespace,
) -> None:
    store = _legacy(git_approved_store)

    result = _upgrade(store, confirm=PHRASE)

    assert is_ok(result), result
    shipped = result["value"]["shipped"]
    assert set(PROFILES) <= set(shipped["adopted"])
    assert set(RETIRED) <= set(shipped["removed"])
    assert COORDINATE in shipped["seeded"]
    assert shipped["customized"] == []
    text = (store / IMPLEMENTER).read_text(encoding="utf-8")
    assert "version: 2" in text
    assert "workspace/profiles/implementer/skills/lean-testing.md" in text
    assert "measure usage" in (store / CTO).read_text(encoding="utf-8")
    assert (store / COORDINATE).is_file()
    for path in RETIRED:
        assert not (store / path).exists()
    changed = records_of(store, "profile.changed")
    assert sorted(record["data"]["target"] for record in changed) == sorted(PROFILES)


def test_upgrade_keeps_a_customised_profile_and_reports_it(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    path = store / IMPLEMENTER
    path.write_text(
        path.read_text(encoding="utf-8").rstrip("\n") + "\n- Our own rule.\n",
        encoding="utf-8",
    )
    before = path.read_bytes()

    result = _upgrade(store, confirm=PHRASE)

    assert is_ok(result), result
    assert path.read_bytes() == before
    shipped = result["value"]["shipped"]
    assert IMPLEMENTER not in shipped["adopted"]
    [entry] = [row for row in shipped["customized"] if row["path"] == IMPLEMENTER]
    assert "Our own rule" in entry["diff"]


def test_upgrade_dry_run_reports_shipped_changes_and_writes_nothing(
    git_approved_store: SimpleNamespace,
) -> None:
    store = _legacy(git_approved_store)
    before = tree_bytes(store)

    result = _upgrade(store, dry_run=True)

    assert is_ok(result), result
    shipped = result["value"]["shipped"]
    assert set(PROFILES) <= set(shipped["adopted"])
    assert COORDINATE in shipped["seeded"]
    assert tree_bytes(store) == before


def test_upgrade_twice_changes_nothing_the_second_time(
    git_approved_store: SimpleNamespace,
) -> None:
    store = _legacy(git_approved_store)
    assert is_ok(_upgrade(store, confirm=PHRASE))
    settled = tree_bytes(store)

    again = _upgrade(store, confirm=PHRASE)

    assert is_ok(again), again
    assert again["value"]["shipped"] == {
        "adopted": [],
        "removed": [],
        "seeded": [],
        "customized": [],
    }
    assert again["value"]["changed"] == [] and again["value"]["next"] == []
    assert tree_bytes(store) == settled


def test_a_half_finished_upgrade_says_so_and_a_rerun_finishes_it(
    git_approved_store: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _legacy(git_approved_store)
    path = store / IMPLEMENTER
    path.write_text(
        path.read_text(encoding="utf-8").rstrip("\n") + "\n- Our own rule.\n",
        encoding="utf-8",
    )
    cause = "pin test-first cannot be resolved"

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise WriteRefused("INTEGRATION_FAILURE", cause)

    monkeypatch.setattr(upgrade_handler, "plan_shipped", refuse)
    failed = _upgrade(store, confirm=PHRASE)

    assert failed["ok"] is False, failed
    error = failed["error"]
    assert error["code"] == "INTEGRATION_FAILURE"
    assert "store files were upgraded" in error["message"]
    assert cause in error["message"]
    assert "rail ops upgrade" in error["message"]
    assert error["details"]["upgraded"] == [IMPLEMENTER]
    assert records_of(store, "upgrade.completed") == []

    monkeypatch.undo()
    result = _upgrade(store, confirm=PHRASE)

    assert is_ok(result), result
    shipped = result["value"]["shipped"]
    assert {CTO, REVIEWER} <= set(shipped["adopted"])
    assert COORDINATE in shipped["seeded"]
    assert [row["path"] for row in shipped["customized"]] == [IMPLEMENTER]
    [record] = records_of(store, "upgrade.completed")
    assert record["data"]["shipped"] == shipped
