"""Doctor findings for broken seats and stale sessions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.doctor.checks import SEVERITIES
from context_rail.gateway.gateway import invoke
from context_rail.model.seats import SEAT_GRANTED
from context_rail.store.sessions import open_session
from helpers import as_human, approve_picture, write_record

SEAT_CODES = {
    "seat_role_unknown": "error",
    "seat_anchor_missing": "error",
    "role_skill_missing": "warning",
    "session_stale": "warning",
}


def doctor(store: Path) -> dict[str, Any]:
    with as_human():
        result = invoke("doctor", {}, store=store, actor=None)
    assert is_ok(result), result
    return result["value"]


def issues(store: Path, code: str) -> list[dict[str, Any]]:
    return [issue for issue in doctor(store)["issues"] if issue["code"] == code]


def rewrite_pack(store: Path, mutate: Any) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        approved = approve_picture(store, actor=None)
    assert is_ok(approved), approved


def test_seat_role_unknown_is_an_error(role_store: SimpleNamespace) -> None:
    write_record(
        role_store.store,
        "seat-ghost-role",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "ghost-1",
            "role": "ghost-role",
            "anchor": "project",
            "granted_by": "human",
        },
        about=(),
        at="2026-11-10T10:10:00Z",
    )

    found = issues(role_store.store, "seat_role_unknown")

    assert found
    assert all(issue["severity"] == "error" for issue in found)
    assert all(issue["fix"] for issue in found)
    assert {issue["details"]["seat"] for issue in found} == {"ghost-1"}
    assert found[0]["details"]["role"] == "ghost-role"


def test_seat_anchor_missing_is_an_error(role_store: SimpleNamespace) -> None:
    write_record(
        role_store.store,
        "seat-ghost-1",
        SEAT_GRANTED,
        {
            "schema": 1,
            "seat": "ghost-1",
            "role": "implementer",
            "anchor": "missing-task",
            "granted_by": "human",
        },
        about=(),
        at="2026-11-10T10:10:00Z",
    )

    found = issues(role_store.store, "seat_anchor_missing")

    assert found
    assert found[0]["severity"] == "error"
    assert found[0]["details"]["anchor"] == "missing-task"
    assert found[0]["fix"]


def test_role_skill_missing_is_a_warning(role_store: SimpleNamespace) -> None:
    rewrite_pack(
        role_store.store,
        lambda document: document["operating"]["roles"]["reviewer"].__setitem__(
            "skills", ["missing-skill"]
        ),
    )

    found = issues(role_store.store, "role_skill_missing")

    assert found
    assert found[0]["severity"] == "warning"
    assert "missing-skill" in found[0]["message"]
    assert found[0]["fix"]


def test_session_stale_is_a_warning(role_store: SimpleNamespace) -> None:
    open_session(
        role_store.store,
        role_store.orders_seat,
        ttl_s=10,
        now=1000.0,
    )

    found = issues(role_store.store, "session_stale")

    assert found
    assert found[0]["severity"] == "warning"
    assert role_store.orders_seat in found[0]["message"]
    assert found[0]["fix"]


def test_legacy_store_has_no_seat_findings(leaf_item: SimpleNamespace) -> None:
    codes = {issue["code"] for issue in doctor(leaf_item.store)["issues"]}

    assert not (codes & set(SEAT_CODES))


def test_every_new_code_is_in_severities() -> None:
    for code, severity in SEAT_CODES.items():
        assert SEVERITIES[code] == severity
