"""Doctor findings for a profile that pins a skill note the store does not have."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.cto import parse_profile
from context_rail.store.doc import parse_doc
from helpers import as_human

IMPLEMENTER = "workspace/profiles/implementer/profile.md"


def doctor(store: Path) -> dict[str, Any]:
    with as_human():
        result = invoke("doctor", {}, store=store, actor=None)
    assert is_ok(result), result
    return result["value"]


def missing_pin_issues(store: Path) -> list[dict[str, Any]]:
    return [
        issue
        for issue in doctor(store)["issues"]
        if issue["code"] == "profile_skill_missing"
    ]


def implementer_pin(store: Path) -> str:
    doc = parse_doc(IMPLEMENTER, (store / IMPLEMENTER).read_bytes())
    return parse_profile(dict(doc.fields), body=doc.body).skills[0]


def test_a_profile_pin_without_a_note_is_a_warning(approved_store: Path) -> None:
    pin = implementer_pin(approved_store)
    (approved_store / pin).unlink()
    reindexed = invoke("reindex", {}, store=approved_store, actor="human")
    assert reindexed["ok"] is True, reindexed

    found = missing_pin_issues(approved_store)

    assert len(found) == 1
    assert found[0]["severity"] == "warning"
    assert found[0]["path"] == IMPLEMENTER
    assert pin in found[0]["message"]
    assert found[0]["fix"] == f"run rail ops upgrade, or add the skill note {pin}"
    assert found[0]["details"] == {"profile": IMPLEMENTER, "skill": pin}


def test_a_seeded_store_has_no_missing_profile_skill(approved_store: Path) -> None:
    assert missing_pin_issues(approved_store) == []
