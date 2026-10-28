"""Protected writes: the human-only floor, typed phrases and the done gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter, load_yaml
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


def _write_check(store: Path, slug: str, item: str, *, acceptance: bool) -> None:
    """Hand-write one Check document, as a human editor would."""
    front = {
        "primitive": "check",
        "preset": "check",
        "schema": 1,
        "mode": "human",
        "acceptance": acceptance,
        "verifies": [f"[[{item}]]"],
    }
    path = store / "checks" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n# " + slug + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _repoint_checks(store: Path) -> None:
    """Change the check placement so a relocate has somewhere to move them."""
    path = store / "rail.yaml"
    manifest = load_yaml(path.read_text(encoding="utf-8"))
    manifest["placement"]["check"] = "verification/{slug}.md"
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _record_count(store: Path) -> int:
    return len(list((store / "records").rglob("*.json")))


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


def test_cto_cannot_convert_a_check_into_an_acceptance_one(approved_store: Path) -> None:
    store = approved_store
    _write_check(store, "signoff", "chase-overdue", acceptance=False)
    before = _digest(store, "checks/signoff.md")
    etag = invoke("show", {"slug": "signoff"}, store=store)["value"]["doc"]["etag"]

    refused = invoke(
        "update",
        {"slug": "signoff", "set": ["acceptance=true"], "expect": etag},
        store=store,
        actor="cto",
    )

    assert refused["error"]["code"] == "PERMISSION_DENIED", refused
    assert _digest(store, "checks/signoff.md") == before
    assert (
        invoke("show", {"slug": "charter"}, store=store)["value"]["approval"]["state"]
        == "approved"
    )


def test_human_conversion_to_acceptance_needs_the_slug_phrase(approved_store: Path) -> None:
    store = approved_store
    _write_check(store, "signoff", "chase-overdue", acceptance=False)
    before = invoke("show", {"slug": "charter"}, store=store)["value"]["approval"]
    body = _digest(store, "checks/signoff.md")
    args = {
        "slug": "signoff",
        "set": ["acceptance=true"],
        "expect": invoke("show", {"slug": "signoff"}, store=store)["value"]["doc"]["etag"],
    }

    missing = invoke("update", args, store=store, actor="human")
    assert missing["error"]["code"] == "VALIDATION_FAILED", missing
    assert _digest(store, "checks/signoff.md") == body

    wrong = invoke(
        "update", {**args, "confirm": "approve signoff"}, store=store, actor="human"
    )
    assert wrong["error"]["code"] == "VALIDATION_FAILED", wrong
    assert _digest(store, "checks/signoff.md") == body

    accepted = invoke(
        "update", {**args, "confirm": "APPROVE signoff"}, store=store, actor="human"
    )
    assert is_ok(accepted), accepted
    shown = invoke("show", {"slug": "signoff"}, store=store)["value"]
    assert shown["doc"]["fields"]["acceptance"] is True

    after = invoke("show", {"slug": "charter"}, store=store)["value"]["approval"]
    assert after["state"] == "mismatch"
    assert "checks/signoff.md" in after["protected_hashes"]
    for rel in PROTECTED:
        assert after["protected_hashes"][rel] == before["protected_hashes"][rel]


def test_cto_cannot_create_an_acceptance_check_through_add(approved_store: Path) -> None:
    store = approved_store
    records = _record_count(store)
    args = {
        "preset": "check",
        "title": "Accept login",
        "set": ["acceptance=true"],
        "rel": ["verifies=chase-overdue"],
    }

    refused = invoke("add", args, store=store, actor="cto")

    assert refused["error"]["code"] == "PERMISSION_DENIED", refused
    assert not (store / "checks" / "accept-login.md").exists()
    assert _record_count(store) == records

    missing = invoke("add", args, store=store, actor="human")
    assert missing["error"]["code"] == "VALIDATION_FAILED", missing
    assert not (store / "checks" / "accept-login.md").exists()

    created = invoke(
        "add", {**args, "confirm": "APPROVE accept-login"}, store=store, actor="human"
    )
    assert is_ok(created), created
    assert (store / "checks" / "accept-login.md").is_file()
    protected = invoke("show", {"slug": "charter"}, store=store)["value"]["approval"]
    assert "checks/accept-login.md" in protected["protected_hashes"]


def test_relocating_an_acceptance_check_is_human_only(approved_store: Path) -> None:
    store = approved_store
    _write_check(store, "accept-login", "chase-overdue", acceptance=True)
    _repoint_checks(store)
    before = _digest(store, "checks/accept-login.md")

    refused = invoke("relocate", {}, store=store, actor="cto")

    assert refused["error"]["code"] == "PERMISSION_DENIED", refused
    assert _digest(store, "checks/accept-login.md") == before
    assert not (store / "verification").exists()

    missing = invoke("relocate", {}, store=store, actor="human")
    assert missing["error"]["code"] == "VALIDATION_FAILED", missing
    assert not (store / "verification").exists()

    moved = invoke(
        "relocate", {"confirm": "APPROVE accept-login"}, store=store, actor="human"
    )
    assert is_ok(moved), moved
    assert (store / "verification" / "accept-login.md").is_file()
    assert not (store / "checks" / "accept-login.md").exists()


def test_an_ordinary_relocate_stays_cto_alone(approved_store: Path) -> None:
    store = approved_store
    assert is_ok(
        invoke("add", {"preset": "task", "title": "Movable"}, store=store, actor="cto")
    )
    path = store / "rail.yaml"
    manifest = load_yaml(path.read_text(encoding="utf-8"))
    manifest["presets"]["task"] = "work/task/{slug}.md"
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n"
    )

    moved = invoke("relocate", {}, store=store, actor="cto")

    assert is_ok(moved), moved
    assert (store / "work" / "task" / "movable.md").is_file()


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
