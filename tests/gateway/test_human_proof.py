"""Human-only decisions in role mode need human proof, never a name."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag
from helpers import as_human, approve_picture, open_test_session

APPROVE = {
    "target": "charter",
    "confirm": "APPROVE charter",
    "confirmer": "seat",
}


def edit_roles(store: Path, change: Any) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(document["operating"]["roles"])
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )


def add_human_role(store: Path) -> None:
    def change(roles: dict[str, Any]) -> None:
        roles["human-lead"] = {
            "anchor": "project",
            "reach": {"read": ["project"], "write": ["project"]},
            "can": {"grant": []},
            "human": True,
            "workspace": "none",
        }
        roles["lead"]["can"]["grant"].append("human-lead")

    edit_roles(store, change)


def as_seat(store: Path, seat: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def test_a_seat_cannot_approve_even_with_the_phrase(
    role_store: SimpleNamespace,
) -> None:
    refused = as_seat(role_store.store, role_store.lead, "approve", APPROVE)

    assert refused["error"]["code"] == "PERMISSION_DENIED"


def test_a_session_caller_with_a_tty_is_not_human(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, role_store.lead)

    with caller_identity(None, None, session=token, tty=True):
        refused = invoke("approve", APPROVE, store=role_store.store)

    assert refused["error"]["code"] == "PERMISSION_DENIED"


def test_anonymous_with_the_phrase_is_refused(role_store: SimpleNamespace) -> None:
    refused = invoke("approve", APPROVE, store=role_store.store)

    assert refused["error"]["code"] == "PERMISSION_DENIED"


def test_human_channel_with_the_phrase_succeeds(role_store: SimpleNamespace) -> None:
    with as_human():
        approved = approve_picture(role_store.store, actor=None)

    assert is_ok(approved), approved


def test_granting_a_human_role_needs_the_human(role_store: SimpleNamespace) -> None:
    add_human_role(role_store.store)
    args = {"seat": "human-2", "role": "human-lead", "anchor": "project"}

    refused = as_seat(role_store.store, role_store.lead, "seat grant", args)
    with as_human():
        allowed = invoke("seat grant", args, store=role_store.store, actor=None)

    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert "human-only" in refused["error"]["message"]
    assert is_ok(allowed), allowed


def test_rail_actor_human_env_is_ignored(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")

    refused = invoke("approve", APPROVE, store=role_store.store)

    assert refused["error"]["code"] == "PERMISSION_DENIED"


def test_pack_use_needs_the_human_channel(role_store: SimpleNamespace) -> None:
    current = etag((role_store.store / "rail.yaml").read_bytes())
    args = {
        "name": "scrum",
        "expect": current,
        "dry_run": True,
        "confirm": "USE PACK scrum",
    }

    refused = as_seat(role_store.store, role_store.lead, "pack use", args)
    with as_human():
        allowed = invoke("pack use", args, store=role_store.store, actor=None)

    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert is_ok(allowed), allowed


def test_a_terminal_human_satisfies_a_human_gate(approved_store: Path) -> None:
    """The human channel is the proof; an unnamed terminal human still is human."""
    from helpers import touch_doc

    store = approved_store
    added = invoke("add", {"preset": "task", "title": "T"}, store=store, actor="cto")
    assert is_ok(added), added
    touch_doc(store, "charter.md", gates=_human_done_gate(store))
    assert is_ok(approve_picture(store))

    with as_human():
        for status in ("doing", "done"):
            shown = invoke("show", {"slug": "t"}, store=store, actor=None)
            assert is_ok(shown), shown
            moved = invoke(
                "move",
                {"slug": "t", "status": status, "expect": shown["value"]["doc"]["etag"]},
                store=store,
                actor=None,
            )
            assert is_ok(moved), moved


def _human_done_gate(store: Path) -> dict[str, Any]:
    """The store's gates with ``item_done`` needing a human and nothing else."""
    document = yaml.safe_load(
        (store / "charter.md").read_text(encoding="utf-8").split("---")[1]
    )
    gates = dict(document["gates"])
    gates["item_done"] = {
        "require_checks": False,
        "require_acceptance": False,
        "require_review": False,
        "require_human": True,
    }
    return gates
