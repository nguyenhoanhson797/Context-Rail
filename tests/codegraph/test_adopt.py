"""``rail ops codegraph adopt``: move a store off the hand-written code map."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.gateway.handlers.codegraph import adopt_fingerprint

LEGACY = Path(__file__).parents[1] / "fixtures" / "legacy_profiles" / "basic" / "workspace"
IMPLEMENTER = "workspace/profiles/implementer/profile.md"
REVIEWER = "workspace/profiles/reviewer/profile.md"
CTO = "workspace/cto/profile.md"
RETIRED = (
    "workspace/cto/skills/code-map.md",
    "workspace/profiles/implementer/skills/code-map.md",
    "workspace/profiles/reviewer/skills/code-map.md",
)


@pytest.fixture
def legacy_store(git_approved_store: SimpleNamespace) -> Path:
    """A store whose profiles and skills are the ones the old basic pack shipped."""
    store = git_approved_store.store
    shutil.copytree(LEGACY, store / "workspace", dirs_exist_ok=True)
    return store


def _adopt(store: Path, **args: Any) -> dict[str, Any]:
    return invoke("codegraph adopt", args, store=store, actor="human")


def _codes(store: Path) -> set[str]:
    issues = invoke("doctor", {}, store=store, actor="human")["value"]["issues"]
    return {issue["code"] for issue in issues}


def test_adopt_rewrites_unmodified_profiles_and_drops_the_code_map(legacy_store: Path) -> None:
    result = _adopt(legacy_store)

    assert result["ok"], result
    value = result["value"]
    assert {IMPLEMENTER, REVIEWER, CTO} <= set(value["adopted"])
    assert set(RETIRED) <= set(value["removed"])
    assert value["customized"] == []
    text = (legacy_store / IMPLEMENTER).read_text(encoding="utf-8")
    assert "code-map" not in text
    assert "Never write to" in text
    assert "version: 2" in text
    for path in RETIRED:
        assert not (legacy_store / path).exists()


def test_adopt_leaves_a_customized_profile_and_reports_the_difference(
    legacy_store: Path,
) -> None:
    path = legacy_store / IMPLEMENTER
    path.write_text(
        path.read_text(encoding="utf-8").rstrip("\n") + "\n- Our own rule.\n",
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")

    value = _adopt(legacy_store)["value"]

    assert path.read_text(encoding="utf-8") == before
    assert IMPLEMENTER not in value["adopted"]
    [entry] = [row for row in value["customized"] if row["path"] == IMPLEMENTER]
    assert "Never write to" in entry["diff"]
    assert {REVIEWER, CTO} <= set(value["adopted"])


def test_adopt_twice_changes_nothing_the_second_time(legacy_store: Path) -> None:
    _adopt(legacy_store)

    value = _adopt(legacy_store)["value"]

    assert value["adopted"] == [] and value["removed"] == []
    assert IMPLEMENTER in value["already"]


def test_adopt_ignores_line_endings_and_bookkeeping_keys(legacy_store: Path) -> None:
    path = legacy_store / IMPLEMENTER
    text = path.read_text(encoding="utf-8").replace("version: 1", "version: 3\nupdated: 2026-01-01")
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    value = _adopt(legacy_store)["value"]

    assert IMPLEMENTER in value["adopted"]


def test_adopt_keeps_the_approval_and_the_doctor_clean(legacy_store: Path) -> None:
    before = invoke("ci", {}, store=legacy_store, actor="human")

    _adopt(legacy_store)
    after = invoke("ci", {}, store=legacy_store, actor="human")

    assert after["ok"] == before["ok"], after
    issues = invoke("doctor", {}, store=legacy_store, actor="human")["value"]["issues"]
    assert not [issue for issue in issues if issue["severity"] == "error"], issues


def test_adopt_dry_run_writes_nothing(legacy_store: Path) -> None:
    before = (legacy_store / IMPLEMENTER).read_bytes()

    value = _adopt(legacy_store, dry_run=True)["value"]

    assert IMPLEMENTER in value["adopted"]
    assert (legacy_store / IMPLEMENTER).read_bytes() == before
    assert (legacy_store / RETIRED[0]).exists()


def test_adopt_is_human_only(legacy_store: Path) -> None:
    result = invoke("codegraph adopt", {}, store=legacy_store, actor="cto")

    assert not result["ok"]
    assert result["error"]["code"] == "PERMISSION_DENIED"


def test_the_doctor_offers_adoption_until_it_is_done(legacy_store: Path) -> None:
    assert "code_map_not_adopted" in _codes(legacy_store)

    _adopt(legacy_store)

    assert "code_map_not_adopted" not in _codes(legacy_store)


def test_setup_names_adopt_for_a_legacy_store(legacy_store: Path) -> None:
    value = invoke("codegraph setup", {}, store=legacy_store, actor="human")["value"]

    assert "rail ops codegraph adopt" in value["hint"]

    _adopt(legacy_store)
    value = invoke("codegraph setup", {}, store=legacy_store, actor="human")["value"]
    assert "hint" not in value


def test_a_fresh_store_needs_no_adoption(git_approved_store: SimpleNamespace) -> None:
    store = git_approved_store.store

    assert "code_map_not_adopted" not in _codes(store)
    value = _adopt(store)["value"]
    assert value["adopted"] == [] and value["removed"] == []


def test_the_fingerprint_ignores_endings_version_and_updated() -> None:
    base = "---\nname: x\nversion: 1\nupdated: a\n---\nbody\n"
    other = "---\nname: x\nversion: 7\nupdated: b\n---\nbody\n".replace("\n", "\r\n")

    assert adopt_fingerprint(base) == adopt_fingerprint(other)
    assert adopt_fingerprint(base) != adopt_fingerprint(base.replace("body", "more"))


def test_the_readme_names_adopt() -> None:
    readme = Path(__file__).parents[2] / "README.md"

    assert "ops codegraph adopt" in readme.read_text(encoding="utf-8")
