"""A session and the terminal flag travel with a call over the CLI and the daemon."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from context_rail.core.result import is_ok
from context_rail.gateway.daemon import spawn_daemon
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import try_daemon_invoke
from helpers import actors_for, open_test_session, run_cli, serving_daemon


def add_args(preset: str, title: str, parent: str) -> dict[str, object]:
    return {"preset": preset, "title": title, "rel": [f"part_of={parent}"]}


def test_cli_passes_the_session_from_the_environment(
    role_store: SimpleNamespace, daemon_enabled: None
) -> None:
    token = open_test_session(role_store.store, role_store.orders_seat)
    with serving_daemon(role_store.store):
        proc = run_cli(
            role_store.store.parent,
            "add",
            "story",
            "T15 from the CLI",
            "--rel",
            f"part_of={role_store.orders}",
            store=role_store.store,
            env={"RAIL_SESSION": token},
        )

    assert proc.returncode == 0, proc.stderr
    added = json.loads(proc.stdout)
    assert is_ok(added), added
    assert role_store.orders_seat in actors_for(role_store.store, added["value"]["slug"])


def test_two_callers_with_different_sessions_through_one_daemon(
    role_store: SimpleNamespace, daemon_enabled: None
) -> None:
    orders = open_test_session(role_store.store, role_store.orders_seat)
    billing = open_test_session(role_store.store, role_store.billing_seat)
    with serving_daemon(role_store.store):
        with caller_identity(None, None, session=orders):
            first = try_daemon_invoke(
                "add",
                add_args("story", "T15 orders", role_store.orders),
                store=role_store.store,
                actor=None,
            )
        with caller_identity(None, None, session=billing):
            second = try_daemon_invoke(
                "add",
                add_args("story", "T15 billing", role_store.billing),
                store=role_store.store,
                actor=None,
            )

    assert first is not None and is_ok(first), first
    assert second is not None and is_ok(second), second
    assert role_store.orders_seat in actors_for(role_store.store, first["value"]["slug"])
    assert role_store.billing_seat in actors_for(
        role_store.store, second["value"]["slug"]
    )


def test_the_daemon_never_caches_identity(
    role_store: SimpleNamespace, daemon_enabled: None
) -> None:
    orders = open_test_session(role_store.store, role_store.orders_seat)
    with serving_daemon(role_store.store):
        with caller_identity(None, None, session=orders):
            signed = try_daemon_invoke(
                "add",
                add_args("story", "T15 signed", role_store.orders),
                store=role_store.store,
                actor=None,
            )
        unsigned = try_daemon_invoke(
            "add",
            add_args("story", "T15 unsigned", role_store.orders),
            store=role_store.store,
            actor=None,
        )

    assert signed is not None and is_ok(signed), signed
    assert unsigned is not None and is_ok(unsigned), unsigned
    assert role_store.orders_seat in actors_for(role_store.store, signed["value"]["slug"])
    assert role_store.orders_seat not in actors_for(
        role_store.store, unsigned["value"]["slug"]
    )


def test_the_daemon_environment_has_no_session_variables(
    role_store: SimpleNamespace,
    popen_calls: list[tuple[list[str], dict[str, object]]],
    monkeypatch,
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "implementer")
    monkeypatch.setenv("RAIL_SESSION", "x" * 43)
    monkeypatch.setenv("RAIL_RUN", "run-1")
    monkeypatch.setenv("RAIL_KEEP_ME", "yes")

    assert spawn_daemon(role_store.store) is True

    (_, options), = popen_calls
    for name in ("RAIL_ACTOR", "RAIL_SESSION", "RAIL_RUN"):
        assert name not in options["env"]
    assert options["env"]["RAIL_KEEP_ME"] == "yes"


def test_actor_flag_in_role_mode_over_the_cli_is_refused(
    role_store: SimpleNamespace,
) -> None:
    proc = run_cli(
        role_store.store.parent,
        "--actor",
        "cto",
        "seat",
        "list",
        store=role_store.store,
    )

    assert proc.returncode == 1
    refused = json.loads(proc.stdout)
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["reason"] == "actor_flag_refused"
