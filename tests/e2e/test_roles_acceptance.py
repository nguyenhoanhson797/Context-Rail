"""One role-mode store through grants, scoped work, review, merge and revocation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import (
    FakeDroid,
    approve_picture,
    as_human,
    init_store,
    latest_record,
    open_test_session,
    records_of,
    run_cli,
)


def _seat(
    token: str, store: Path, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    with caller_identity(None, None, session=token):
        return invoke(name, args or {}, store=store)


def _human(
    store: Path, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    with as_human():
        return invoke(name, args or {}, store=store, actor=None)


def _refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def _etag(store: Path, slug: str) -> str:
    shown = _human(store, "show", {"slug": slug})
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def _status(store: Path, slug: str) -> str:
    shown = _human(store, "show", {"slug": slug})
    assert is_ok(shown), shown
    return shown["value"]["doc"]["status"]


def test_multi_manager_role_and_seat_acceptance(
    role_git_store: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = role_git_store.store
    task = role_git_store.task
    orders = role_git_store.orders
    o1, o2 = role_git_store.o1, role_git_store.o2
    b1, b2 = role_git_store.b1, role_git_store.b2
    probe = FakeDroid(
        tmp_path.parent / f"{tmp_path.name}-droid",
        ["success", "review_approve"],
    )
    monkeypatch.setenv("RAIL_DROID_BIN", str(probe.script))
    worktrees = tmp_path.parent / f"{tmp_path.name}-worktrees"
    monkeypatch.setenv("RAIL_WORKTREE_ROOT", str(worktrees))

    listed = _human(store, "seat list")
    assert is_ok(listed), listed
    assert [row["seat"] for row in listed["value"]["seats"]] == [
        role_git_store.lead,
        role_git_store.orders_seat,
        role_git_store.billing_seat,
    ]

    lead_token = open_test_session(store, role_git_store.lead)
    orders_token = open_test_session(store, role_git_store.orders_seat)
    billing_token = open_test_session(store, role_git_store.billing_seat)

    orders_queue = _seat(orders_token, store, "next")
    billing_queue = _seat(billing_token, store, "next")
    assert is_ok(orders_queue), orders_queue
    assert is_ok(billing_queue), billing_queue
    orders_items = {o1, o2, task}
    assert set(orders_queue["value"]["queue"]) <= orders_items
    assert set(billing_queue["value"]["queue"]) <= {b1, b2}

    lens = run_cli(
        store.parent,
        "mgr-orders",
        "next",
        store=store,
        env={"RAIL_SESSION": lead_token, "RAIL_NO_DAEMON": "1"},
    )
    assert lens.returncode == 0, lens.stdout + lens.stderr
    lens_queue = set(json.loads(lens.stdout)["value"]["queue"])
    assert lens_queue <= orders_items
    blind = run_cli(
        store.parent,
        "mgr-orders",
        "show",
        b1,
        store=store,
        env={"RAIL_SESSION": lead_token, "RAIL_NO_DAEMON": "1"},
    )
    assert blind.returncode == 1
    assert json.loads(blind.stdout)["error"]["details"]["reason"] == "out_of_reach"

    assert _refusal(_seat(orders_token, store, "show", {"slug": b1})) == (
        "PERMISSION_DENIED",
        "out_of_reach",
    )
    b1_etag = _etag(store, b1)
    assert _refusal(
        _seat(
            orders_token,
            store,
            "update",
            {"slug": b1, "set": ["title=Billing rewrite"], "expect": b1_etag},
        )
    ) == ("PERMISSION_DENIED", "write_out_of_reach")
    assert _refusal(
        _seat(
            orders_token,
            store,
            "link",
            {
                "src": o1,
                "rel": "part_of",
                "dst": b1,
                "expect": _etag(store, o1),
            },
        )
    ) == ("PERMISSION_DENIED", "write_out_of_reach")
    assert _refusal(
        _seat(
            orders_token,
            store,
            "dispatch",
            {"item": b1, "expect": b1_etag, "key": "accept-cross-area"},
        )
    ) == ("PERMISSION_DENIED", "write_out_of_reach")

    added = _seat(
        orders_token,
        store,
        "add",
        {
            "preset": "story",
            "title": "Orders acceptance",
            "rel": [f"part_of={orders}"],
        },
    )
    assert is_ok(added), added
    assert added["value"]["slug"] == "orders-acceptance"
    assert _refusal(
        _seat(orders_token, store, "add", {"preset": "profile", "title": "Wide"})
    ) == ("PERMISSION_DENIED", "preset_not_in_can_add")
    assert _seat(
        orders_token,
        store,
        "pack use",
        {"name": "basic", "expect": "0" * 16},
    )["error"]["code"] == "PERMISSION_DENIED"
    assert _refusal(
        _seat(
            orders_token,
            store,
            "add",
            {
                "preset": "event",
                "title": "Fake seat record",
                "set": ["kind=seat.granted"],
            },
        )
    ) == ("PERMISSION_DENIED", "preset_not_in_can_add")

    with caller_identity(None, None, session=orders_token):
        flagged = invoke(
            "add",
            {
                "preset": "story",
                "title": "Actor flag",
                "rel": [f"part_of={orders}"],
            },
            store=store,
            actor="human",
        )
    assert _refusal(flagged) == ("PERMISSION_DENIED", "actor_flag_refused")

    monkeypatch.setenv("RAIL_ACTOR", "human")
    try:
        anonymous = invoke(
            "add",
            {
                "preset": "story",
                "title": "Env actor",
                "rel": [f"part_of={orders}"],
            },
            store=store,
        )
    finally:
        monkeypatch.delenv("RAIL_ACTOR", raising=False)
    assert _refusal(anonymous) == ("SESSION_REQUIRED", "session_required")

    probe.set_behaviors(
        [
            {
                "mode": "success",
                "rail": [
                    "--store",
                    str(store),
                    "report",
                    b1,
                    "--run",
                    "01J00000000000000000000000",
                    "--report-file",
                    str(tmp_path / "probe-report.json"),
                ],
            },
            "review_approve",
        ]
    )
    dispatched = _seat(
        orders_token,
        store,
        "dispatch",
        {
            "item": task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "accept-dispatch",
        },
    )
    assert is_ok(dispatched), dispatched
    assert dispatched["value"]["status"] == "verified"
    run = dispatched["value"]["run"]
    run_data = latest_record(store, "run.completed")["data"]
    assert run_data["seat_role"] == "implementer"
    assert run_data["seat"].startswith("implementer@")
    assert run_data["dispatcher"] == role_git_store.orders_seat
    report_id = run_data.get("report")
    assert report_id, run_data
    assert any(row["id"] == report_id for row in records_of(store, "report.filed"))
    child_env = probe.log()[0]["env"]
    assert child_env["RAIL_SESSION"]
    assert "RAIL_ACTOR" not in child_env
    run_seat_write = json.loads(
        (probe.root / "fake-droid-rail.json").read_text(encoding="utf-8")
    )
    assert run_seat_write["exit"] == 1
    run_seat_error = json.loads(run_seat_write["stdout"])["error"]
    assert (run_seat_error["code"], run_seat_error["details"]["reason"]) == (
        "PERMISSION_DENIED",
        "write_out_of_reach",
    )

    reviewed = _seat(
        orders_token,
        store,
        "review",
        {
            "item": task,
            "run": run,
            "profile": "validator",
            "role": "reviewer",
            "key": "accept-review",
        },
    )
    assert is_ok(reviewed), reviewed
    assert reviewed["value"]["verdict"] == "approve"
    assert reviewed["value"]["status"] == "approved"

    merged = _seat(
        orders_token,
        store,
        "merge",
        {
            "item": task,
            "run": run,
            "expect": _etag(store, task),
            "key": "accept-merge",
        },
    )
    assert is_ok(merged), merged
    assert merged["value"]["merged"] is True
    assert merged["value"]["closed"] == task
    assert _status(store, task) == "done"

    second = _seat(
        orders_token,
        store,
        "add",
        {
            "preset": "task",
            "title": "Code reach task",
            "rel": [f"part_of={orders}"],
        },
    )
    assert is_ok(second), second
    second_slug = second["value"]["slug"]
    spec = role_git_store.root.parent / "role-unit.json"
    checked = _seat(
        orders_token,
        store,
        "check",
        {
            "slug": "role-unit-2",
            "target": second_slug,
            "spec_file": str(spec),
            "expect": "absent",
        },
    )
    assert is_ok(checked), checked

    probe.set_behaviors([{"mode": "success", "work": "second candidate\n"}])
    second_run = _seat(
        orders_token,
        store,
        "dispatch",
        {
            "item": second_slug,
            "expect": _etag(store, second_slug),
            "profile": "worker",
            "key": "accept-dispatch-2",
        },
    )
    assert is_ok(second_run), second_run
    assert second_run["value"]["status"] == "verified", second_run

    pack_path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    document["operating"]["roles"]["implementer"]["reach"]["code"]["write"] = [
        "src/orders/**"
    ]
    pack_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        approved = approve_picture(store, actor=None)
    assert is_ok(approved), approved

    denied = _seat(
        orders_token,
        store,
        "merge",
        {
            "item": second_slug,
            "run": second_run["value"]["run"],
            "expect": _etag(store, second_slug),
            "key": "accept-code-reach",
        },
    )
    assert not is_ok(denied), denied
    error = denied["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATED"
    assert error["details"]["paths"] == ["fake-droid-work.txt"]
    assert error["details"]["reach"] == ["src/orders/**"]

    revoked = _human(store, "seat revoke", {"seat": role_git_store.lead})
    assert is_ok(revoked), revoked
    assert revoked["value"]["live"] is False
    remaining = {
        row["seat"] for row in _human(store, "seat list")["value"]["seats"]
    }
    assert role_git_store.orders_seat not in remaining
    assert role_git_store.billing_seat not in remaining
    assert _refusal(
        _seat(orders_token, store, "show", {"slug": o1})
    ) == ("PERMISSION_DENIED", "session_invalid")

    legacy_root = tmp_path.parent / f"{tmp_path.name}-legacy"
    legacy = init_store(legacy_root, "basic")
    legacy_add = invoke(
        "add", {"preset": "task", "title": "Legacy task"}, store=legacy, actor="cto"
    )
    assert is_ok(legacy_add), legacy_add
    assert is_ok(invoke("next", {}, store=legacy, actor="cto"))
    legacy_seats = invoke("seat list", {}, store=legacy, actor="cto")
    assert _refusal(legacy_seats) == ("VALIDATION_FAILED", "no_roles")
    legacy_lens = run_cli(legacy_root, "mgr-orders", store=legacy)
    assert legacy_lens.returncode == 1
    assert json.loads(legacy_lens.stdout)["error"]["details"]["reason"] == "no_roles"
    shutil.rmtree(worktrees, ignore_errors=True)
