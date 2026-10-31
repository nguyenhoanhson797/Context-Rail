from __future__ import annotations

import http.client
import json
from urllib.parse import urlsplit

from context_rail.gateway.gateway import invoke
from context_rail.ui.server import MAX_BODY


def _call(
    base: str,
    path: str,
    *,
    token: str | None,
    method: str = "GET",
    body: dict | None = None,
    host: str | None = None,
) -> tuple[int, dict, dict]:
    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Rail-Token"] = token
    if host is not None:
        headers["Host"] = host
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    headers_out = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    return response.status, payload, headers_out


def test_context_needs_a_token_and_never_runs_doctor(ui_server) -> None:
    status, payload, _ = _call(ui_server.base, "/api/context", token=None)
    assert status == 401
    assert payload["error"]["code"] == "PERMISSION_DENIED"

    status, payload, headers = _call(
        ui_server.base, "/api/context", token=ui_server.token
    )
    assert status == 200, payload
    assert payload["ok"] is True
    value = payload["value"]
    assert value["schema"] == 1
    assert "counts" in value["status"] and "items" in value
    assert "doctor" not in json.dumps(value).lower()
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


def test_tools_and_reads_go_through_the_gateway(ui_server) -> None:
    status, payload, _ = _call(ui_server.base, "/api/tools", token=ui_server.token)
    assert status == 200
    names = {tool["name"] for tool in payload["value"]}
    assert {"find", "migrate", "ui serve"} <= names

    status, payload, _ = _call(
        ui_server.base,
        "/api/tool/find",
        token=ui_server.token,
        method="POST",
        body={"preset": "task"},
    )
    assert status == 200, payload
    assert payload["ok"] is True
    assert payload["value"]["items"] == []

    added = invoke(
        "add", {"preset": "task", "title": "UI task"}, store=ui_server.store, actor="cto"
    )
    assert added["ok"] is True, added
    status, payload, _ = _call(
        ui_server.base, "/api/doc/ui-task", token=ui_server.token
    )
    assert status == 200
    assert payload["value"]["doc"]["slug"] == "ui-task"


def test_hosts_and_reserved_arguments_are_refused(ui_server) -> None:
    status, payload, _ = _call(
        ui_server.base, "/api/context", token=ui_server.token, host="evil.example"
    )
    assert status == 403

    status, payload, _ = _call(
        ui_server.base,
        "/api/tool/find",
        token=ui_server.token,
        method="POST",
        body={"actor": "cto"},
    )
    assert status == 400
    assert payload["error"]["code"] == "VALIDATION_FAILED"

    status, payload, _ = _call(
        ui_server.base,
        "/api/tool/mcp%20serve",
        token=ui_server.token,
        method="POST",
        body={},
    )
    assert status == 403
    assert payload["error"]["code"] == "FORBIDDEN"

    status, _, _ = _call(ui_server.base, "/nope", token=ui_server.token)
    assert status == 404


def test_the_body_cap_refuses_before_reading(ui_server) -> None:
    parts = urlsplit(ui_server.base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    connection.putrequest("POST", "/api/tool/find")
    connection.putheader("X-Rail-Token", ui_server.token)
    connection.putheader("Content-Length", str(MAX_BODY + 1))
    connection.putheader("Content-Type", "application/json")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    response.read()
    connection.close()


def test_the_page_is_self_contained(ui_server) -> None:
    status, payload, headers = _call(
        ui_server.base, "/", token=ui_server.token
    )
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "Context Rail" in payload
