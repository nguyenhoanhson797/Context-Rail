"""The loopback UI: the built React shell plus the engine's JSON contract."""

from __future__ import annotations

import json

from context_rail.gateway.gateway import invoke
from context_rail.ui.server import MAX_BODY
from helpers import ui_call, ui_oversize_probe

_call = ui_call


def test_context_needs_a_token_and_names_the_pack(ui_server) -> None:
    status, payload, _ = _call(ui_server.base, "/api/context", token=None)
    assert status == 401
    assert payload["code"] == "PERMISSION_DENIED"

    status, payload, headers = _call(
        ui_server.base, "/api/context", token=ui_server.token
    )
    assert status == 200, payload
    assert payload["schema"] == 4
    assert payload["pack"]
    assert isinstance(payload["tasks"], list)
    assert isinstance(payload["phases"], list)
    assert isinstance(payload["workflows"], list)
    assert payload["statuses"]
    assert payload["status_categories"]
    assert "doctor" not in json.dumps(payload).lower()
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


def test_health_and_named_reads_use_explicit_envelopes(ui_server) -> None:
    status, health, _ = _call(ui_server.base, "/api/health", token=ui_server.token)
    assert status == 200, health
    assert health["schema"] == "ui-health-v1"
    assert health["state"] in {"ok", "unknown", "error"}

    status, doctor, _ = _call(
        ui_server.base, "/api/read/doctor", token=ui_server.token
    )
    assert status == 200, doctor
    assert doctor["status"] == "ok"
    assert isinstance(doctor["issues"], list)

    status, queue, _ = _call(ui_server.base, "/api/read/next", token=ui_server.token)
    assert status == 200, queue
    assert queue["status"] == "ok"
    assert isinstance(queue["queue"], list)

    status, missing, _ = _call(
        ui_server.base, "/api/read/atlas", token=ui_server.token
    )
    assert status == 200, missing
    assert missing["status"] == "error"
    assert "phase" in missing["error"]


def test_a_document_and_a_move_go_through_the_gateway(ui_server) -> None:
    added = invoke(
        "add", {"preset": "task", "title": "UI task"}, store=ui_server.store, actor="cto"
    )
    assert added["ok"], added

    status, shown, _ = _call(
        ui_server.base, "/api/doc/ui-task", token=ui_server.token
    )
    assert status == 200, shown
    assert shown["status"] == "ok"
    doc = shown["doc"]
    assert doc["title"] == "UI task"
    assert doc["workflow"]
    assert doc["etag"]

    status, moved, _ = _call(
        ui_server.base,
        "/api/tool/change%20move",
        token=ui_server.token,
        method="POST",
        body={"args": {"slug": "ui-task", "status": "doing", "expect": doc["etag"]}},
    )
    assert status == 200, moved
    assert moved["status"] == "ok"
    assert moved["item_status"] == "doing"


def test_the_tool_catalog_is_the_current_surface(ui_server) -> None:
    status, payload, _ = _call(ui_server.base, "/api/tools", token=ui_server.token)
    assert status == 200, payload
    assert payload["status"] == "ok"
    names = {tool["name"] for tool in payload["tools"]}
    assert {"find", "show", "move", "types", "next", "explain", "why", "do"} <= names
    assert "ui serve" not in names
    assert payload["confirm"] == []


def test_hosts_reserved_arguments_and_forbidden_tools_are_refused(ui_server) -> None:
    status, payload, _ = _call(
        ui_server.base, "/api/context", token=ui_server.token, host="evil.example"
    )
    assert status == 403
    assert payload["code"] == "FORBIDDEN"

    status, payload, _ = _call(
        ui_server.base,
        "/api/tool/find",
        token=ui_server.token,
        method="POST",
        body={"args": {"actor": "cto"}},
    )
    assert status == 200
    assert payload["status"] == "error"
    assert payload["code"] == "VALIDATION_FAILED"

    for name in ("mcp%20serve", "ui%20serve"):
        status, payload, _ = _call(
            ui_server.base,
            f"/api/tool/{name}",
            token=ui_server.token,
            method="POST",
            body={"args": {}},
        )
        assert status == 403
        assert payload["code"] == "FORBIDDEN"

    status, _, _ = _call(ui_server.base, "/nope", token=ui_server.token)
    assert status == 404


def test_the_body_cap_refuses_before_reading(ui_server) -> None:
    assert ui_oversize_probe(ui_server.base, ui_server.token, MAX_BODY + 1) == 413


def test_the_built_page_is_served_with_the_token(ui_server) -> None:
    status, page, headers = _call(ui_server.base, "/", token=ui_server.token)

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "Context Rail" in page
    assert ui_server.token in page
    assert "/assets/" in page
    assert "rail_ui_token=" in headers["set-cookie"]


def test_an_unknown_asset_is_not_served(ui_server) -> None:
    status, payload, _ = _call(
        ui_server.base, "/assets/../../rail.yaml", token=ui_server.token
    )
    assert status == 404
    assert payload["code"] == "NOT_FOUND"
