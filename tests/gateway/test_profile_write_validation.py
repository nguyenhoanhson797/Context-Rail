"""Actor profiles are validated where they are written; legacy rights only narrow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import parse_doc


def profile_fields(store: Path, slug: str = "implementer") -> tuple[str, dict[str, Any]]:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    rel = shown["value"]["doc"]["path"]
    doc = parse_doc(rel, (store / rel).read_bytes())
    return rel, dict(doc.fields)


def set_args(fields: dict[str, Any]) -> list[str]:
    return [f"{key}={json.dumps(value)}" for key, value in fields.items()]


def profile_etag(store: Path, slug: str = "implementer") -> str:
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def prompt_file(store: Path) -> str:
    path = store.parent / "profile-prompt.md"
    path.write_text("# Role\n\nDo the work.\n", encoding="utf-8")
    return str(path)


def test_add_profile_missing_schema_is_refused_at_write_time(
    approved_store: Path,
) -> None:
    _rel, fields = profile_fields(approved_store)
    fields.pop("schema", None)
    added = invoke(
        "add",
        {
            "preset": "profile",
            "title": "Bad Profile",
            "set": set_args(fields),
            "body_file": prompt_file(approved_store),
            "confirm": "APPROVE bad-profile",
        },
        store=approved_store,
        actor="human",
    )

    assert added["error"]["code"] == "VALIDATION_FAILED", added
    assert not (approved_store / "workspace" / "profiles" / "bad-profile.md").exists()
    doctor = invoke("doctor", {}, store=approved_store, actor="cto")
    assert is_ok(doctor), doctor
    assert doctor["value"]["issues"] == []


def test_update_profile_to_an_invalid_value_is_refused(approved_store: Path) -> None:
    before = profile_etag(approved_store)
    updated = invoke(
        "update",
        {
            "slug": "implementer",
            "expect": before,
            "set": ["schema=2"],
            "confirm": "APPROVE implementer",
        },
        store=approved_store,
        actor="human",
    )

    assert updated["error"]["code"] == "SCHEMA_UNSUPPORTED", updated
    assert profile_etag(approved_store) == before


def test_legacy_add_of_a_profile_is_human_only(approved_store: Path) -> None:
    _rel, fields = profile_fields(approved_store)
    args = {
        "preset": "profile",
        "title": "Second Implementer",
        "set": set_args(fields),
        "body_file": prompt_file(approved_store),
    }

    as_cto = invoke("add", args, store=approved_store, actor="cto")
    as_human = invoke(
        "add",
        {**args, "confirm": "APPROVE second-implementer"},
        store=approved_store,
        actor="human",
    )

    assert as_cto["error"]["code"] == "PERMISSION_DENIED"
    assert "human-only" in as_cto["error"]["message"]
    assert is_ok(as_human), as_human


def test_legacy_update_that_adds_a_method_is_human_only(approved_store: Path) -> None:
    _rel, fields = profile_fields(approved_store)
    methods = list(fields["allowed_methods"]) + ["merge"]
    updated = invoke(
        "update",
        {
            "slug": "implementer",
            "expect": profile_etag(approved_store),
            "set": [f"allowed_methods={json.dumps(methods)}"],
        },
        store=approved_store,
        actor="cto",
    )

    assert updated["error"]["code"] == "PERMISSION_DENIED"
    assert "human-only" in updated["error"]["message"]


def test_legacy_update_that_removes_a_method_is_allowed_for_the_cto(
    approved_store: Path,
) -> None:
    _rel, fields = profile_fields(approved_store)
    methods = [name for name in fields["allowed_methods"] if name != "report"]
    updated = invoke(
        "update",
        {
            "slug": "implementer",
            "expect": profile_etag(approved_store),
            "set": [f"allowed_methods={json.dumps(methods)}"],
        },
        store=approved_store,
        actor="cto",
    )

    assert is_ok(updated), updated


def test_a_manager_cannot_add_a_profile_with_merge(approved_store: Path) -> None:
    _rel, fields = profile_fields(approved_store)
    methods = list(fields["allowed_methods"]) + ["merge"]
    added = invoke(
        "add",
        {
            "preset": "profile",
            "title": "Manager Profile",
            "set": set_args({**fields, "allowed_methods": methods}),
        },
        store=approved_store,
        actor="cto",
    )

    assert added["error"]["code"] == "PERMISSION_DENIED"
    assert "human-only" in added["error"]["message"]
