"""Skill and profile changes are versioned with their full before and after text."""

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


def _update(store: Path, slug: str, expect: str, body_file: Path, key: str) -> dict:
    return invoke(
        "update",
        {
            "slug": slug,
            "expect": expect,
            "body_file": str(body_file),
            "key": key,
        },
        store=store,
        actor="cto",
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _show(store: Path, slug: str) -> dict:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]


def test_skill_change_is_versioned_with_full_text(
    skill_store: Path, tmp_path: Path
) -> None:
    rel = "workspace/skills/test-first.md"
    before_bytes = (skill_store / rel).read_bytes()
    before_text = before_bytes.decode("utf-8")
    before_etag = etag(before_bytes)
    assert "version: 1" in before_text.split("---")[1]

    new_body = tmp_path / "new-skill-body.md"
    new_body.write_text(
        "# Test first\n\nWrite the failing test, watch it fail, then make it pass.\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _update(skill_store, "test-first", before_etag, new_body, "sv1")
    assert is_ok(result), result

    after_bytes = (skill_store / rel).read_bytes()
    after_text = after_bytes.decode("utf-8")
    assert "version: 2" in after_text.split("---")[1]

    records = records_of(skill_store, "skill.changed")
    assert len(records) == 1
    data = records[0]["data"]
    assert set(data) == CHANGE_KEYS
    assert data["schema"] == 1
    assert data["target_class"] == "skill"
    assert data["target"] == rel
    assert (data["before"], data["after"]) == (1, 2)
    assert data["before_text"] == before_text
    assert data["after_text"] == after_text
    assert data["before_text"].startswith("---\n")
    assert data["after_text"].startswith("---\n")
    assert data["before_digest"] == _digest(before_text)
    assert data["after_digest"] == _digest(after_text)
    assert data["before_etag"] == before_etag
    assert data["after_etag"] == etag(after_bytes)
    assert data["restored_from_version"] is None
    assert data["reports"] == []


def test_second_change_keeps_the_first_full_text(
    skill_store: Path, tmp_path: Path
) -> None:
    rel = "workspace/skills/test-first.md"
    first_body = tmp_path / "first.md"
    first_body.write_text("# Test first\n\nFirst edit.\n", encoding="utf-8", newline="\n")
    first = _update(
        skill_store, "test-first", etag((skill_store / rel).read_bytes()), first_body, "sv2"
    )
    assert is_ok(first), first
    first_text = (skill_store / rel).read_text(encoding="utf-8")

    second_body = tmp_path / "second.md"
    second_body.write_text("# Test first\n\nSecond edit.\n", encoding="utf-8", newline="\n")
    second = _update(
        skill_store, "test-first", etag((skill_store / rel).read_bytes()), second_body, "sv3"
    )
    assert is_ok(second), second

    records = records_of(skill_store, "skill.changed")
    assert len(records) == 2
    later = max(records, key=lambda row: row["id"])
    assert later["data"]["before"] == 2 and later["data"]["after"] == 3
    assert later["data"]["before_text"] == first_text
    assert later["data"]["before_digest"] == _digest(first_text)


def test_profile_change_is_versioned(skill_store: Path, tmp_path: Path) -> None:
    added = invoke(
        "add",
        {
            "preset": "profile",
            "title": "reviewer",
            "set": ["version=1", "recipe=reviewer"],
        },
        store=skill_store,
        actor="cto",
    )
    assert is_ok(added), added
    rel = "workspace/profiles/reviewer/profile.md"
    assert (skill_store / rel).is_file()

    body = tmp_path / "reviewer-body.md"
    body.write_text("Review the candidate diff.\n", encoding="utf-8", newline="\n")
    result = _update(
        skill_store, "reviewer", etag((skill_store / rel).read_bytes()), body, "pv1"
    )
    assert is_ok(result), result

    records = records_of(skill_store, "profile.changed")
    assert len(records) == 1
    data = records[0]["data"]
    assert set(data) == CHANGE_KEYS
    assert data["target_class"] == "profile"
    assert data["target"] == rel
    assert (data["before"], data["after"]) == (1, 2)
    assert data["before_text"] and data["after_text"]
    assert data["after_text"].startswith("---\n")


def test_a_managed_version_cannot_be_set_by_hand(
    skill_store: Path, tmp_path: Path
) -> None:
    rel = "workspace/skills/test-first.md"
    result = invoke(
        "update",
        {
            "slug": "test-first",
            "expect": etag((skill_store / rel).read_bytes()),
            "set": ["version=9"],
            "key": "sv4",
        },
        store=skill_store,
        actor="cto",
    )
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(skill_store, "skill.changed") == []


def test_a_new_skill_starts_at_version_one(store_factory: Any) -> None:
    store = store_factory(docs=1)
    added = invoke(
        "add",
        {"preset": "skill", "title": "small-steps", "set": ["description=One step at a time"]},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    shown = _show(store, "small-steps")
    fields = shown["doc"]["fields"]
    assert fields["version"] == 1
    assert fields["schema"] == 1
    assert fields["name"] == "small-steps"


def test_a_skill_name_must_stay_its_file_stem(store_factory: Any) -> None:
    store = store_factory(docs=1)
    refused = invoke(
        "add",
        {
            "preset": "skill",
            "title": "small-steps",
            "set": ["description=One step at a time", "name=other"],
        },
        store=store,
        actor="cto",
    )
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "workspace" / "skills" / "small-steps.md").exists()

    added = invoke(
        "add",
        {"preset": "skill", "title": "small-steps", "set": ["description=One step"]},
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    rel = "workspace/skills/small-steps.md"
    renamed = invoke(
        "update",
        {
            "slug": "small-steps",
            "expect": etag((store / rel).read_bytes()),
            "set": ["name=other"],
            "key": "sv-name",
        },
        store=store,
        actor="cto",
    )
    assert renamed["error"]["code"] == "VALIDATION_FAILED"
    assert records_of(store, "skill.changed") == []


def test_a_stale_etag_wins_over_the_version_refusal(skill_store: Path) -> None:
    rel = "workspace/skills/test-first.md"
    current = etag((skill_store / rel).read_bytes())
    stale = ("0" if current[0] != "0" else "1") + current[1:]
    result = invoke(
        "update",
        {
            "slug": "test-first",
            "expect": stale,
            "set": ["version=9"],
            "key": "sv-stale",
        },
        store=skill_store,
        actor="cto",
    )
    assert result["error"]["code"] == "CONFLICT"
    assert records_of(skill_store, "skill.changed") == []


def test_a_non_positive_version_is_refused(store_factory: Any) -> None:
    store = store_factory(docs=1)
    refused = invoke(
        "add",
        {"preset": "skill", "title": "bad-version", "set": ["version=0"]},
        store=store,
        actor="cto",
    )
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "workspace" / "skills" / "bad-version.md").exists()

