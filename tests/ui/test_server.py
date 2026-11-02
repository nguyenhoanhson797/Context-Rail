from __future__ import annotations

import json

from context_rail.gateway.gateway import invoke
from context_rail.ui.server import MAX_BODY
from helpers import ui_call, ui_oversize_probe

_call = ui_call


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
    assert {"read find", "ops migrate", "serve ui"} <= names

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

    status, payload, _ = _call(
        ui_server.base,
        "/api/tool/read%20find",
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
    assert ui_oversize_probe(ui_server.base, ui_server.token, MAX_BODY + 1) == 413


def test_the_page_is_self_contained(ui_server) -> None:
    status, payload, headers = _call(
        ui_server.base, "/", token=ui_server.token
    )
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "Context Rail" in payload


def _raw(
    base: str,
    method: str,
    path: str,
    headers: list[tuple[str, str]],
    body: bytes | None = None,
) -> tuple[int, bytes]:
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.putrequest(method, path)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_a_negative_content_length_is_rejected_without_reading(ui_server) -> None:
    from urllib.parse import urlsplit

    port = urlsplit(ui_server.base).port
    status, _ = _raw(
        ui_server.base,
        "POST",
        "/api/tool/find",
        [
            ("Host", f"127.0.0.1:{port}"),
            ("X-Rail-Token", ui_server.token),
            ("Content-Length", "-1"),
            ("Content-Type", "application/json"),
        ],
    )

    assert status == 400


def test_the_body_cap_is_exact(ui_server, monkeypatch) -> None:
    import context_rail.ui.server as server_module
    from urllib.parse import urlsplit

    monkeypatch.setattr(server_module, "MAX_BODY", 32)
    port = urlsplit(ui_server.base).port

    exact = b'{"x":"' + b"a" * 24 + b'"}'
    assert len(exact) == 32
    status, _ = _raw(
        ui_server.base,
        "POST",
        "/api/tool/find",
        [
            ("Host", f"127.0.0.1:{port}"),
            ("X-Rail-Token", ui_server.token),
            ("Content-Length", str(len(exact))),
            ("Content-Type", "application/json"),
        ],
        exact,
    )
    assert status != 413

    over = exact + b" "
    status, _ = _raw(
        ui_server.base,
        "POST",
        "/api/tool/find",
        [
            ("Host", f"127.0.0.1:{port}"),
            ("X-Rail-Token", ui_server.token),
            ("Content-Length", str(len(over))),
            ("Content-Type", "application/json"),
        ],
        over,
    )
    assert status == 413


def test_a_second_fixed_port_bind_is_a_typed_refusal(ui_server) -> None:
    import pytest

    from context_rail.store.write import WriteRefused
    from context_rail.ui.server import create_server

    port = ui_server.server.server_address[1]

    with pytest.raises(WriteRefused) as caught:
        create_server(ui_server.store, port=port)

    assert caught.value.code == "CONFLICT"
    assert caught.value.details["port"] == port


def test_a_non_ascii_token_is_rejected_safely(ui_server) -> None:
    status, payload, _ = _call(
        ui_server.base, "/api/context", token="t\u00f6k\u00e9n"
    )
    assert status == 401
    assert payload["error"]["code"] == "PERMISSION_DENIED"


def test_the_ipv6_loopback_host_and_origin_are_refused(ui_server) -> None:
    from urllib.parse import urlsplit

    port = urlsplit(ui_server.base).port
    status, _, _ = _call(
        ui_server.base, "/api/context", token=ui_server.token, host=f"[::1]:{port}"
    )
    assert status == 403

    status, _ = _raw(
        ui_server.base,
        "GET",
        "/api/context",
        [
            ("Host", f"127.0.0.1:{port}"),
            ("Origin", f"http://[::1]:{port}"),
            ("X-Rail-Token", ui_server.token),
        ],
    )
    assert status == 403


def test_the_page_catches_a_failed_fetch() -> None:
    from context_rail.ui.page import PAGE

    assert "async function call" in PAGE
    assert "catch (error)" in PAGE
    assert "NETWORK" in PAGE
