"""The local agent credential: one human setup step, then tokenless calls act."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import as_human, open_test_session, refusal

from context_rail import cli
from context_rail.core.locate import AGENT_TOKEN, local_dir
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke


def _enable(role_store: SimpleNamespace, seat: str = "local-agent") -> dict:
    with as_human():
        return invoke(
            "agent enable",
            {"seat": seat, "role": "lead", "anchor": "project"},
            store=role_store.store,
            actor=None,
        )


def test_agent_enable_writes_a_credential_that_reads_and_writes(
    role_store: SimpleNamespace,
) -> None:
    enabled = _enable(role_store)

    assert enabled["ok"] is True, enabled
    assert enabled["value"]["enabled"] is True
    assert enabled["value"]["seat"] == "local-agent"
    assert "token" not in enabled["value"]
    token = (local_dir(role_store.store) / AGENT_TOKEN).read_text(encoding="utf-8").strip()
    assert token

    with caller_identity(None, None, session=token):
        mapped = invoke("map", {"max_chars": 4000}, store=role_store.store)
        added = invoke(
            "add",
            {
                "preset": "story",
                "title": "Local agent story",
                "rel": [f"part_of={role_store.orders}"],
            },
            store=role_store.store,
        )

    assert mapped["ok"] is True, mapped
    assert mapped["value"]["total"] >= 3
    assert added["ok"] is True, added


def test_agent_show_names_the_seat_without_revealing_the_token(
    role_store: SimpleNamespace,
) -> None:
    _enable(role_store)

    shown = invoke("agent show", {}, store=role_store.store)

    assert shown["ok"] is True, shown
    assert shown["value"]["enabled"] is True
    assert shown["value"]["seat"] == "local-agent"
    assert "token" not in shown["value"]


def test_agent_disable_closes_the_credential_and_its_session(
    role_store: SimpleNamespace,
) -> None:
    _enable(role_store)
    path = local_dir(role_store.store) / AGENT_TOKEN
    token = path.read_text(encoding="utf-8").strip()

    with as_human():
        disabled = invoke("agent disable", {}, store=role_store.store, actor=None)

    assert disabled["ok"] is True, disabled
    assert disabled["value"]["enabled"] is False
    assert not path.exists()
    with caller_identity(None, None, session=token):
        stale = invoke("map", {}, store=role_store.store)
    assert refusal(stale) == ("PERMISSION_DENIED", "session_invalid")


def test_agent_enable_is_human_only(role_store: SimpleNamespace) -> None:
    token = open_test_session(role_store.store, "lead-1")
    with caller_identity(None, None, session=token):
        refused = invoke(
            "agent enable",
            {"seat": "local-agent", "role": "lead", "anchor": "project"},
            store=role_store.store,
        )

    assert refusal(refused) == ("PERMISSION_DENIED", "ops_human_only")


def test_cli_session_falls_back_to_the_credential_only_off_a_terminal(
    role_store: SimpleNamespace, monkeypatch
) -> None:
    _enable(role_store)
    token = (local_dir(role_store.store) / AGENT_TOKEN).read_text(encoding="utf-8").strip()
    monkeypatch.delenv("RAIL_SESSION", raising=False)

    monkeypatch.setattr(cli, "_terminal", lambda: False)
    assert cli.caller_session(None, role_store.store) == token

    monkeypatch.setattr(cli, "_terminal", lambda: True)
    assert cli.caller_session(None, role_store.store) is None
