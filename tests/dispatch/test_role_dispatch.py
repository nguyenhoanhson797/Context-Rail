"""Role-driven dispatch: pick the worker role, open its run seat, record both."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from helpers import as_human, open_test_session


def as_seat(
    store: Path, seat: str, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def refusal(result: dict[str, Any]) -> tuple[str | None, Any]:
    assert not is_ok(result), result
    error = result["error"]
    return error["code"], (error.get("details") or {}).get("reason")


def test_dispatch_without_can_dispatch_is_refused(role_store: SimpleNamespace) -> None:
    with as_human():
        granted = invoke(
            "seat grant",
            {
                "seat": "impl-1",
                "role": "implementer",
                "anchor": role_store.o1,
            },
            store=role_store.store,
            actor=None,
        )
    assert is_ok(granted), granted
    etag = as_seat(role_store.store, "impl-1", "show", {"slug": role_store.o1})
    assert is_ok(etag)

    dispatched = as_seat(
        role_store.store,
        "impl-1",
        "dispatch",
        {
            "item": role_store.o1,
            "expect": etag["value"]["doc"]["etag"],
            "key": "role-guard-dispatch",
        },
    )

    assert refusal(dispatched) == ("PERMISSION_DENIED", "method_not_in_role")


def test_dispatch_of_an_item_outside_reach_is_refused(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        shown = invoke("show", {"slug": role_store.b1}, store=role_store.store, actor=None)
    assert is_ok(shown)
    dispatched = as_seat(
        role_store.store,
        role_store.orders_seat,
        "dispatch",
        {
            "item": role_store.b1,
            "expect": shown["value"]["doc"]["etag"],
            "key": "role-out-of-reach",
        },
    )

    assert refusal(dispatched) == ("PERMISSION_DENIED", "write_out_of_reach")


def test_run_record_carries_seat_fields_and_a_legacy_role(
    role_git_store: SimpleNamespace,
) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "role-dispatch-1",
        },
    )

    assert is_ok(dispatched), dispatched
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (role_git_store.store / "records").rglob("*.json")
    ]
    runs = [row for row in rows if row.get("kind") == "run.completed"]
    data = runs[-1]["data"]
    assert data["role"] == "implementer"
    assert data["seat"].startswith("implementer@")
    assert data["seat_role"] == "implementer"
    assert data["dispatcher"] == role_git_store.lead


def test_widening_profile_is_refused_at_dispatch(
    role_git_store: SimpleNamespace,
) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "cto",
            "key": "role-widening",
        },
    )

    assert refusal(dispatched) == ("VALIDATION_FAILED", "profile_widens_role")


def test_ambiguous_role_lists_candidates(role_store: SimpleNamespace) -> None:
    import yaml

    from helpers import approve_picture

    path = role_store.store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    roles = document["operating"]["roles"]
    roles["implementer-2"] = dict(roles["implementer"])
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        assert is_ok(approve_picture(role_store.store, actor=None))
        task = invoke(
            "add",
            {"preset": "task", "title": "Ambiguous task", "rel": [f"part_of={role_store.o1}"]},
            store=role_store.store,
            actor=None,
        )
        assert is_ok(task), task
        shown = invoke("show", {"slug": task["value"]["slug"]}, store=role_store.store, actor=None)

    dispatched = as_seat(
        role_store.store,
        role_store.lead,
        "dispatch",
        {
            "item": task["value"]["slug"],
            "expect": shown["value"]["doc"]["etag"],
            "profile": "implementer",
            "key": "role-ambiguous",
        },
    )

    assert refusal(dispatched) == ("CONFLICT", "ambiguous_role")
    assert set(dispatched["error"]["details"]["candidates"]) == {
        "implementer",
        "implementer-2",
    }


def test_ledger_and_budget_rollups_still_count_a_role_mode_run(
    role_git_store: SimpleNamespace,
) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "role-ledger",
        },
    )
    assert is_ok(dispatched), dispatched

    ledger = as_seat(role_git_store.store, role_git_store.lead, "ledger", {"limit": 20})

    assert is_ok(ledger), ledger
    roles = {row["role"] for row in ledger["value"]["runs"]}
    assert "implementer" in roles


def test_a_human_role_cannot_be_dispatched_by_a_seat(
    role_git_store: SimpleNamespace,
) -> None:
    import yaml

    from helpers import approve_picture

    path = role_git_store.store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    role = dict(document["operating"]["roles"]["implementer"])
    role["human"] = True
    document["operating"]["roles"]["human-worker"] = role
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        assert is_ok(approve_picture(role_git_store.store, actor=None))

    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "role": "human-worker",
            "key": "role-human",
        },
    )

    assert refusal(dispatched) == ("PERMISSION_DENIED", "human_role_requires_human")


def test_legacy_dispatch_is_unchanged(
    leaf_item: SimpleNamespace, fake_droid: Any, droid_env: dict[str, str]
) -> None:
    dispatched = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": leaf_item.etag, "key": "legacy-dispatch"},
        store=leaf_item.store,
        actor="cto",
    )

    assert is_ok(dispatched), dispatched
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (leaf_item.store / "records").rglob("*.json")
    ]
    data = [row for row in rows if row.get("kind") == "run.completed"][-1]["data"]
    assert data["role"] == "implementer"
    assert "seat" not in data and "seat_role" not in data and "dispatcher" not in data


def test_review_needs_a_read_workspace_role(role_git_store: SimpleNamespace) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "role-review-dispatch",
        },
    )
    assert is_ok(dispatched), dispatched
    run_id = dispatched["value"]["run"]

    reviewed = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "review",
        {
            "item": role_git_store.task,
            "run": run_id,
            "role": "implementer",
            "key": "role-review-1",
        },
    )

    assert refusal(reviewed) == ("VALIDATION_FAILED", "role_workspace")
