"""A charter delegates named operational actions to one lead, once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helpers import approve_picture, as_human, as_seat, refusal

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter, load_yaml
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag


def _delegate(
    store: Path, to: str, actions: list[str], *, role_mode: bool = True
) -> None:
    """Add the charter's delegation block and have the human approve it."""
    path = store / "charter.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end > 0
    fields = load_yaml(text[4:end])
    fields["delegation"] = {"to": to, "actions": actions}
    path.write_text(
        "---\n" + dump_frontmatter(fields) + "---\n" + text[end + 5 :],
        encoding="utf-8",
        newline="\n",
    )
    if role_mode:
        with as_human():
            approved = approve_picture(store, actor=None)
    else:
        approved = approve_picture(store, actor="human")
    assert is_ok(approved), approved


def _used(store: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "delegation.used":
            found.append(payload)
    return found


def test_init_writes_a_delegation_from_the_answers(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    answers = json.loads(json.dumps(picture_answers))
    answers["charter"]["delegation"] = {
        "to": "cto",
        "actions": ["ops upgrade"],
    }
    answers_path = tmp_path / "answers-delegation.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8", newline="\n")

    created = invoke(
        "init",
        {
            "dir": str(tmp_path),
            "answers_file": str(answers_path),
            "confirm": "APPROVE charter",
            "confirmer": "Ada",
        },
        actor="human",
    )

    assert is_ok(created), created
    store = tmp_path / "context-rail"
    charter = (store / "charter.md").read_text(encoding="utf-8")
    assert "delegation:" in charter
    assert "to: cto" in charter
    assert "ops upgrade" in charter
    shown = invoke("show", {"slug": "charter"}, store=store)
    assert shown["value"]["approval"]["state"] == "approved"


def test_the_human_sets_the_delegation_and_approval_makes_it_live(role_store) -> None:
    charter = role_store.store / "charter.md"
    before = etag(charter.read_bytes())

    with as_human():
        result = invoke(
            "delegate",
            {
                "to": role_store.lead,
                "action": ["ops adapters render"],
                "confirm": f"DELEGATE {role_store.lead}",
                "expect": before,
            },
            store=role_store.store,
        )

    assert is_ok(result), result
    assert result["value"]["delegation"] == {
        "to": role_store.lead,
        "actions": ["ops adapters render"],
    }
    assert result["value"]["etag"] != before

    dormant = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"dry_run": True}
    )
    assert not is_ok(dormant)
    assert refusal(dormant) == ("PERMISSION_DENIED", "delegation_unapproved")

    with as_human():
        approved = approve_picture(role_store.store, actor=None)
    assert is_ok(approved), approved

    live = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"dry_run": True}
    )
    assert is_ok(live), live


def test_a_replayed_delegate_key_returns_the_current_state(role_store) -> None:
    charter = role_store.store / "charter.md"
    args = {
        "to": role_store.lead,
        "action": ["ops adapters render"],
        "confirm": f"DELEGATE {role_store.lead}",
        "expect": etag(charter.read_bytes()),
        "key": "delegate-once",
    }
    with as_human():
        first = invoke("delegate", args, store=role_store.store)
    assert is_ok(first), first
    with as_human():
        approved = approve_picture(role_store.store, actor=None)
    assert is_ok(approved), approved

    with as_human():
        second = invoke(
            "delegate",
            {**args, "expect": etag(charter.read_bytes())},
            store=role_store.store,
        )

    assert is_ok(second), second
    assert second["value"]["etag"] == etag(charter.read_bytes())
    assert second["value"]["next"] == []


def test_the_human_can_remove_the_delegation(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])
    charter = role_store.store / "charter.md"

    with as_human():
        result = invoke(
            "delegate",
            {
                "to": role_store.lead,
                "remove": True,
                "confirm": f"DELEGATE {role_store.lead}",
                "expect": etag(charter.read_bytes()),
            },
            store=role_store.store,
        )

    assert is_ok(result), result
    assert result["value"]["delegation"] is None

    refused = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"dry_run": True}
    )
    assert not is_ok(refused)
    assert refusal(refused) == ("PERMISSION_DENIED", "ops_human_only")


def test_an_agent_cannot_set_a_delegation(role_store) -> None:
    charter = role_store.store / "charter.md"

    result = as_seat(
        role_store.store,
        role_store.lead,
        "delegate",
        {
            "to": role_store.lead,
            "action": ["ops upgrade"],
            "confirm": f"DELEGATE {role_store.lead}",
            "expect": etag(charter.read_bytes()),
        },
    )

    assert not is_ok(result)
    assert result["error"]["code"] == "PERMISSION_DENIED"


def test_the_human_cannot_delegate_an_action_outside_the_allowlist(role_store) -> None:
    charter = role_store.store / "charter.md"

    with as_human():
        result = invoke(
            "delegate",
            {
                "to": role_store.lead,
                "action": ["ops pack use"],
                "confirm": f"DELEGATE {role_store.lead}",
                "expect": etag(charter.read_bytes()),
            },
            store=role_store.store,
        )

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert "cannot be delegated" in result["error"]["message"]


def test_the_lead_may_render_adapters_when_the_charter_delegates_it(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])

    result = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"dry_run": True}
    )

    assert is_ok(result), result


def test_a_seat_that_is_not_the_delegate_is_refused(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])

    result = as_seat(
        role_store.store, role_store.orders_seat, "adapters sync", {"dry_run": True}
    )

    assert not is_ok(result)
    assert refusal(result) == ("PERMISSION_DENIED", "ops_human_only")


def test_an_action_outside_the_list_is_still_human_only(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])

    result = as_seat(
        role_store.store, role_store.lead, "upgrade", {"confirm": "UPGRADE store"}
    )

    assert not is_ok(result)
    assert refusal(result) == ("PERMISSION_DENIED", "human_channel_required")


def test_a_stale_charter_approval_dormants_the_delegation(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])
    charter = role_store.store / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nA later, unapproved edit.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"dry_run": True}
    )

    assert not is_ok(result)
    assert refusal(result) == ("PERMISSION_DENIED", "delegation_unapproved")


def test_a_delegated_action_records_who_ran_it_for_whom(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])

    result = as_seat(role_store.store, role_store.lead, "adapters sync", {})

    assert is_ok(result), result
    records = _used(role_store.store)
    assert len(records) == 1
    data = records[0]["data"]
    assert data["action"] == "ops adapters render"
    assert data["delegate"] == role_store.lead
    assert data["approved_by"] == "validator"
    assert data["charter_approval"]

    gate = as_seat(role_store.store, role_store.lead, "ci", {})
    assert gate["ok"], gate
    assert gate["value"]["counts"]["error"] == 0


def test_a_delegated_lead_cannot_force_adapters(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops adapters render"])

    result = as_seat(
        role_store.store, role_store.lead, "adapters sync", {"force": True}
    )

    assert not is_ok(result)
    assert "forcing generated adapter files" in result["error"]["message"]


def test_a_delegated_upgrade_leaves_its_audit_record(approved_store: Path) -> None:
    _delegate(approved_store, "cto", ["ops upgrade"], role_mode=False)

    result = invoke(
        "upgrade",
        {"confirm": "UPGRADE store"},
        store=approved_store,
        actor="cto",
    )

    assert is_ok(result), result
    records = _used(approved_store)
    assert len(records) == 1
    assert records[0]["data"]["action"] == "ops upgrade"
    assert records[0]["data"]["delegate"] == "cto"


def test_the_lead_may_upgrade_when_the_charter_delegates_it(role_store) -> None:
    _delegate(role_store.store, role_store.lead, ["ops upgrade"])

    result = as_seat(role_store.store, role_store.lead, "upgrade", {"dry_run": True})

    assert is_ok(result), result
    assert result["value"]["delegation"]["delegate"] == role_store.lead


def test_every_allowlisted_action_has_a_gateway_mapping() -> None:
    from context_rail.gateway.gateway import _DELEGATED_TOOLS
    from context_rail.model.delegation import DELEGABLE_ACTIONS

    assert set(_DELEGATED_TOOLS.values()) == set(DELEGABLE_ACTIONS)


def test_a_named_actor_may_upgrade_when_the_charter_delegates_it(
    approved_store: Path,
) -> None:
    _delegate(approved_store, "cto", ["ops upgrade"], role_mode=False)

    result = invoke("upgrade", {"dry_run": True}, store=approved_store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["delegation"]["delegate"] == "cto"
    assert result["value"]["delegation"]["approved_by"] == "validator"
