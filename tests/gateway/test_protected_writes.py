"""Protected writes: the human-only floor, typed phrases and the done gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke

PROTECTED = (
    "charter.md",
    "project/send-invoice.md",
    "project/ship-invoicing.md",
    "project/invoices-on-autopilot.md",
    "project/wont.md",
)


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _digest(store: Path, rel: str) -> str:
    return hashlib.sha256((store / rel).read_bytes()).hexdigest()


def _body_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "new-body.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_cto_cannot_change_protected_meaning(
    approved_store: Path, tmp_path: Path
) -> None:
    before = {rel: _digest(approved_store, rel) for rel in PROTECTED}
    body = _body_file(tmp_path, "Rewritten by the CTO.\n")

    attempts: list[dict[str, Any]] = [
        {
            "name": "update",
            "args": {
                "slug": "send-invoice",
                "body_file": str(body),
                "expect": _etag(approved_store, "send-invoice"),
            },
        },
        {
            "name": "update",
            "args": {
                "slug": "send-invoice",
                "set": ["title=Changed"],
                "expect": _etag(approved_store, "send-invoice"),
            },
        },
        {
            "name": "link",
            "args": {
                "src": "send-invoice",
                "rel": "part_of",
                "dst": "ship-invoicing",
                "remove": True,
                "expect": _etag(approved_store, "send-invoice"),
            },
        },
        {"name": "rename", "args": {"old": "send-invoice", "new": "send-invoice-2"}},
        {
            "name": "update",
            "args": {
                "slug": "charter",
                "body_file": str(body),
                "expect": _etag(approved_store, "charter"),
            },
        },
    ]

    for attempt in attempts:
        result = invoke(attempt["name"], attempt["args"], store=approved_store, actor="cto")
        assert result["error"]["code"] == "PERMISSION_DENIED", (attempt, result)

    assert {rel: _digest(approved_store, rel) for rel in PROTECTED} == before


def test_human_protected_write_needs_the_slug_phrase(approved_store: Path) -> None:
    args = {
        "slug": "send-invoice",
        "set": ["title=Send the invoice"],
        "expect": _etag(approved_store, "send-invoice"),
    }
    before = _digest(approved_store, "project/send-invoice.md")

    missing = invoke("update", args, store=approved_store, actor="human")
    assert missing["error"]["code"] == "VALIDATION_FAILED"
    assert _digest(approved_store, "project/send-invoice.md") == before

    wrong = invoke(
        "update", {**args, "confirm": "approve send-invoice"}, store=approved_store, actor="human"
    )
    assert wrong["error"]["code"] == "VALIDATION_FAILED"
    assert _digest(approved_store, "project/send-invoice.md") == before

    accepted = invoke(
        "update",
        {
            "slug": "send-invoice",
            "set": ["title=Send the invoice"],
            "expect": _etag(approved_store, "send-invoice"),
            "confirm": "APPROVE send-invoice",
        },
        store=approved_store,
        actor="human",
    )
    assert is_ok(accepted), accepted
    assert _digest(approved_store, "project/send-invoice.md") != before
    assert (
        invoke("show", {"slug": "charter"}, store=approved_store)["value"]["approval"]["state"]
        == "mismatch"
    )


def test_tags_and_presets_never_grant_protection(approved_store: Path) -> None:
    added = invoke(
        "add",
        {"preset": "task", "title": "Tagged thing", "tag": ["must", "scope"]},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(added), added

    renamed = invoke(
        "update",
        {
            "slug": "tagged-thing",
            "set": ["title=Renamed thing"],
            "expect": _etag(approved_store, "tagged-thing"),
        },
        store=approved_store,
        actor="cto",
    )

    assert is_ok(renamed), renamed
    assert (
        invoke("show", {"slug": "charter"}, store=approved_store)["value"]["approval"]["state"]
        == "approved"
    )


def test_an_unprotected_planning_write_stays_cto_alone(approved_store: Path) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "Ordinary task"}, store=approved_store, actor="cto"
    )
    assert is_ok(added), added

    result = invoke(
        "update",
        {
            "slug": "ordinary-task",
            "set": ["estimate=3"],
            "expect": _etag(approved_store, "ordinary-task"),
        },
        store=approved_store,
        actor="cto",
    )

    assert is_ok(result), result


def test_a_profile_can_only_use_its_allowed_methods(approved_store: Path) -> None:
    refused = invoke(
        "add", {"preset": "task", "title": "Worker item"}, store=approved_store, actor="implementer"
    )
    assert refused["error"]["code"] == "PERMISSION_DENIED"

    allowed = invoke(
        "find", {"preset": "task"}, store=approved_store, actor="implementer"
    )
    assert is_ok(allowed), allowed


def test_move_to_done_cannot_bypass_the_gate(approved_store: Path) -> None:
    added = invoke("add", {"preset": "task", "title": "Leaf work"}, store=approved_store, actor="cto")
    assert is_ok(added), added

    active = invoke(
        "move",
        {"slug": "leaf-work", "status": "doing", "expect": _etag(approved_store, "leaf-work")},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(active), active

    done = invoke(
        "move",
        {"slug": "leaf-work", "status": "done", "expect": _etag(approved_store, "leaf-work")},
        store=approved_store,
        actor="cto",
    )

    assert done["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert done["error"]["details"]["gate"] == "item_done"
    assert done["error"]["details"]["reasons"]
    assert (
        invoke("show", {"slug": "leaf-work"}, store=approved_store)["value"]["doc"]["status"]
        == "doing"
    )


def test_doctor_reports_approval_missing_then_mismatch(
    tmp_path: Path, answers: Path, approved_store: Path
) -> None:
    assert is_ok(
        invoke(
            "init",
            {
                "dir": str(tmp_path / "unapproved"),
                "answers_file": str(answers),
                "confirm": "APPROVE charter",
            },
            actor="human",
        )
    )
    unapproved = tmp_path / "unapproved" / "context-rail"

    missing = invoke("doctor", {}, store=unapproved, actor="cto")["value"]
    codes = {(issue["code"], issue["severity"]) for issue in missing["issues"]}
    assert ("approval_missing", "error") in codes

    path = approved_store / "project" / "send-invoice.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited by hand.\n", encoding="utf-8")
    mismatched = invoke("doctor", {}, store=approved_store, actor="cto")["value"]
    codes = {(issue["code"], issue["severity"]) for issue in mismatched["issues"]}
    assert ("approval_mismatch", "error") in codes
    assert mismatched["counts"]["error"] >= 1
