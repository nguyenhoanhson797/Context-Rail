"""An approval records its signer; a named lead may sign when the charter allows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _approve_args(store: Path) -> dict[str, str]:
    shown = invoke("show", {"slug": "charter"}, store=store)
    assert is_ok(shown), shown
    return {
        "target": "charter",
        "confirm": "APPROVE charter",
        "confirmer": "Ada",
        "expect": shown["value"]["approval"]["snapshot_digest"],
    }


def _approval_record(store: Path) -> dict[str, Any]:
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "approval.recorded":
            return payload
    raise AssertionError("no approval record was written")


def _human_only_store(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> Path:
    answers = json.loads(json.dumps(picture_answers))
    answers["charter"]["approval"] = {"agents": False}
    answers_path = tmp_path / "answers-human-only.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8", newline="\n")
    project = tmp_path / "human-only"
    result = invoke(
        "init",
        {
            "dir": str(project),
            "answers_file": str(answers_path),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    assert is_ok(result), result
    return project / "context-rail"


def test_a_named_cto_records_an_agent_signed_approval(
    unapproved_store: Path,
) -> None:
    result = invoke(
        "approve", _approve_args(unapproved_store), store=unapproved_store, actor="cto"
    )

    assert is_ok(result), result
    value = result["value"]
    assert value["signer"] == "cto"
    assert value["signer_kind"] == "agent"
    assert value["channel"] == "agent"
    assert value["on_behalf_of"] == "Ada"

    record = _approval_record(unapproved_store)
    assert record["data"]["signer"] == "cto"
    assert record["data"]["signer_kind"] == "agent"
    assert record["data"]["channel"] == "agent"
    assert record["data"]["on_behalf_of"] == "Ada"

    shown = invoke("show", {"slug": "charter"}, store=unapproved_store)["value"]
    assert shown["approval"]["state"] == "approved"
    assert shown["approval"]["signer"] == "cto"
    assert shown["approval"]["signer_kind"] == "agent"


def test_a_human_channel_may_approve_without_a_named_actor(
    unapproved_store: Path,
) -> None:
    from context_rail.gateway.execution import human_channel

    args = _approve_args(unapproved_store)
    with human_channel():
        result = invoke("approve", args, store=unapproved_store, actor=None)

    assert is_ok(result), result
    assert result["value"]["signer_kind"] == "human"
    assert result["value"]["channel"] == "ui"


def test_a_human_approval_records_the_human_signer(unapproved_store: Path) -> None:
    result = invoke(
        "approve", _approve_args(unapproved_store), store=unapproved_store, actor="human"
    )

    assert is_ok(result), result
    assert result["value"]["signer"] == "human"
    assert result["value"]["signer_kind"] == "human"
    assert result["value"]["on_behalf_of"] is None


def test_a_non_lead_actor_cannot_sign(unapproved_store: Path) -> None:
    result = invoke(
        "approve",
        _approve_args(unapproved_store),
        store=unapproved_store,
        actor="implementer",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert result["error"]["details"]["reason"] == "not_lead"


def test_a_charter_that_requires_a_human_refuses_an_agent(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    store = _human_only_store(tmp_path, picture_answers)
    args = _approve_args(store)

    refused = invoke("approve", args, store=store, actor="cto")

    assert refused["ok"] is False
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["reason"] == "human_signature_required"

    accepted = invoke("approve", args, store=store, actor="human")
    assert is_ok(accepted), accepted
    assert accepted["value"]["signer_kind"] == "human"
