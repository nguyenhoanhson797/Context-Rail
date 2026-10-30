"""Skill and profile identity changes are versioned, and skills are validated."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag
from helpers import records_of

CHANGE_KEYS = {
    "schema",
    "target_class",
    "target",
    "before",
    "after",
    "before_etag",
    "after_etag",
    "before_text",
    "after_text",
    "before_digest",
    "after_digest",
    "reason",
    "reports",
    "restored_from_version",
}
SKILL_REL = "workspace/skills/test-first.md"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _show(store: Path, slug: str) -> dict:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]


def _add_profile(store: Path, name: str) -> None:
    added = invoke(
        "add",
        {"preset": "profile", "title": name, "set": ["version=1", "recipe=reviewer"]},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added


def test_a_skill_rename_appends_one_versioned_change(
    skill_store: Path,
) -> None:
    before_bytes = (skill_store / SKILL_REL).read_bytes()
    before_text = before_bytes.decode("utf-8")

    result = invoke(
        "rename", {"old": "test-first", "new": "prove-first", "key": "rn1"},
        store=skill_store,
        actor="cto",
    )
    assert is_ok(result), result
    new_rel = "workspace/skills/prove-first.md"
    assert result["value"]["path"] == new_rel
    assert not (skill_store / SKILL_REL).exists()

    after_bytes = (skill_store / new_rel).read_bytes()
    after_text = after_bytes.decode("utf-8")
    shown = _show(skill_store, "prove-first")
    fields = shown["doc"]["fields"]
    assert fields["name"] == "prove-first"
    assert fields["version"] == 2
    assert fields["schema"] == 1

    records = records_of(skill_store, "skill.changed")
    assert len(records) == 1
    data = records[0]["data"]
    assert set(data) == CHANGE_KEYS
    assert data["target_class"] == "skill"
    assert data["target"] == new_rel
    assert (data["before"], data["after"]) == (1, 2)
    assert data["before_text"] == before_text
    assert data["after_text"] == after_text
    assert data["before_digest"] == _digest(before_text)
    assert data["after_digest"] == _digest(after_text)
    assert data["restored_from_version"] is None

    synced = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert is_ok(synced), synced
    assert ".agents/skills/prove-first/SKILL.md" in synced["value"]["written"]
    doctor = invoke("doctor", {}, store=skill_store, actor="human")
    assert is_ok(doctor), doctor
    codes = {issue["code"] for issue in doctor["value"]["issues"]}
    assert "adapter_modified" not in codes
    assert "profile_invalid" not in codes


def test_a_skill_rename_retargets_links_and_keeps_one_change(
    skill_store: Path,
) -> None:
    task = invoke("add", {"preset": "task", "title": "Use the skill"}, store=skill_store, actor="cto")
    assert is_ok(task), task
    linked = invoke(
        "link",
        {
            "src": "use-the-skill",
            "rel": "depends_on",
            "dst": "test-first",
            "expect": _show(skill_store, "use-the-skill")["doc"]["etag"],
            "key": "ln1",
        },
        store=skill_store,
        actor="cto",
    )
    assert is_ok(linked), linked

    result = invoke(
        "rename", {"old": "test-first", "new": "prove-first", "key": "rn2"},
        store=skill_store,
        actor="cto",
    )
    assert is_ok(result), result
    assert result["value"]["rewritten"] >= 1
    rows = records_of(skill_store, "skill.changed")
    assert len([row for row in rows if row["data"]["target_class"] == "skill"]) == 1


def test_a_profile_rename_appends_one_versioned_change(store_factory: Any) -> None:
    store = store_factory(docs=1)
    _add_profile(store, "pick-reviewer")
    rel = "workspace/profiles/pick-reviewer/profile.md"
    before_text = (store / rel).read_text(encoding="utf-8")

    result = invoke(
        "rename", {"old": "pick-reviewer", "new": "gate-reviewer", "key": "rn3"},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result
    new_rel = "workspace/profiles/gate-reviewer/profile.md"
    after_text = (store / new_rel).read_text(encoding="utf-8")

    records = records_of(store, "profile.changed")
    assert len(records) == 1
    data = records[0]["data"]
    assert set(data) == CHANGE_KEYS
    assert data["target_class"] == "profile"
    assert data["target"] == new_rel
    assert (data["before"], data["after"]) == (1, 2)
    assert data["before_text"] == before_text
    assert data["after_text"] == after_text


def test_a_skill_link_appends_one_versioned_change(skill_store: Path) -> None:
    task = invoke("add", {"preset": "task", "title": "Needs the skill"}, store=skill_store, actor="cto")
    assert is_ok(task), task
    before_text = (skill_store / SKILL_REL).read_text(encoding="utf-8")

    result = invoke(
        "link",
        {
            "src": "test-first",
            "rel": "depends_on",
            "dst": "needs-the-skill",
            "expect": etag((skill_store / SKILL_REL).read_bytes()),
            "key": "ln2",
        },
        store=skill_store,
        actor="cto",
    )
    assert is_ok(result), result

    after_text = (skill_store / SKILL_REL).read_text(encoding="utf-8")
    assert _show(skill_store, "test-first")["doc"]["fields"]["version"] == 2
    records = rows = records_of(skill_store, "skill.changed")
    assert len(rows) == 1
    data = records[0]["data"]
    assert set(data) == CHANGE_KEYS
    assert data["before_text"] == before_text
    assert data["after_text"] == after_text
    assert data["reason"] is None


def test_adding_a_skill_without_a_description_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=1)
    refused = invoke(
        "add", {"preset": "skill", "title": "unlabelled"}, store=store, actor="cto"
    )
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "workspace" / "skills" / "unlabelled.md").exists()


def test_adding_a_skill_with_an_unknown_tool_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=1)
    refused = invoke(
        "add",
        {
            "preset": "skill",
            "title": "toolful",
            "set": ["description=Uses a tool", "allowed_tools=[read-cli, no-such-tool]"],
        },
        store=store,
        actor="cto",
    )
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert "no-such-tool" in refused["error"]["message"]
    assert not (store / "workspace" / "skills" / "toolful.md").exists()


def test_a_valid_skill_with_tools_is_accepted(store_factory: Any) -> None:
    store = store_factory(docs=1)
    added = invoke(
        "add",
        {
            "preset": "skill",
            "title": "toolful",
            "set": ["description=Uses a tool", "allowed_tools=[read-cli, ls-cli]"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    fields = _show(store, "toolful")["doc"]["fields"]
    assert fields["allowed_tools"] == ["read-cli", "ls-cli"]


def test_updating_a_skill_to_drop_its_description_is_refused(skill_store: Path) -> None:
    refused = invoke(
        "update",
        {
            "slug": "test-first",
            "expect": etag((skill_store / SKILL_REL).read_bytes()),
            "set": ["description="],
            "key": "sv-bad",
        },
        store=skill_store,
        actor="cto",
    )
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(skill_store, "skill.changed") == []
