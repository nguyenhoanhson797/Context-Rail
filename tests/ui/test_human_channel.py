"""The UI's per-run token is the human channel; the API alone is not."""

from __future__ import annotations

from context_rail.ui import api
from helpers import actors_for, serve_ui, ui_call


def add_body(title: str, parent: str) -> dict[str, object]:
    return {"args": {"preset": "story", "title": title, "rel": [f"part_of={parent}"]}}


def test_a_ui_request_without_the_token_is_refused_before_the_gateway(role_store) -> None:
    with serve_ui(role_store.store) as ui:
        status, payload, _ = ui_call(
            ui.base,
            "/api/tool/add",
            token=None,
            method="POST",
            body=add_body("UI no token", role_store.orders),
        )

    assert status == 401
    assert payload["code"] == "PERMISSION_DENIED"
    assert actors_for(role_store.store, "ui-no-token") == set()


def test_a_ui_request_with_the_token_acts_as_the_human(role_store) -> None:
    with serve_ui(role_store.store) as ui:
        status, payload, _ = ui_call(
            ui.base,
            "/api/tool/add",
            token=ui.token,
            method="POST",
            body=add_body("UI human", role_store.orders),
        )

    assert status == 200, payload
    assert payload["status"] == "ok", payload
    assert "human" in actors_for(role_store.store, "ui-human")


def test_api_call_tool_alone_is_not_a_human_channel(role_store) -> None:
    result = api.call_tool(
        role_store.store,
        "add",
        {
            "preset": "story",
            "title": "UI direct",
            "rel": [f"part_of={role_store.orders}"],
        },
        actor=None,
    )

    assert result["status"] == "ok", result
    assert "human" not in actors_for(role_store.store, "ui-direct")
