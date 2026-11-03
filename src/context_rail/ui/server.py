"""A loopback-only HTTP surface over ``ui.api`` and therefore over ``invoke``."""

from __future__ import annotations

import hmac
import json
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from context_rail.store.write import WriteRefused
from context_rail.ui import api
from context_rail.ui.page import PAGE

__all__ = ["MAX_BODY", "create_server", "serve"]

MAX_BODY = 2 * 1024 * 1024
REQUEST_TIMEOUT_S = 30.0
_COOKIE = "rail_ui_token"
_HEADER = "X-Rail-Token"
_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; connect-src 'self'"
)


def create_server(
    store: Path, *, actor: str = "human", port: int = 0
) -> SimpleNamespace:
    """Bind the UI to loopback and return its server, token and base URL."""
    token = secrets.token_urlsafe(32)
    store = Path(store)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ContextRailUI/1"
        timeout = REQUEST_TIMEOUT_S

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parts = urlsplit(self.path)
            if not self._host_allowed():
                self._error(HTTPStatus.FORBIDDEN, "FORBIDDEN", "host is not loopback")
                return
            if not self._token_ok(parts.query):
                self._error(
                    HTTPStatus.UNAUTHORIZED, "PERMISSION_DENIED", "a UI token is required"
                )
                return
            path = parts.path
            if method == "GET" and path == "/":
                self._html(HTTPStatus.OK, PAGE)
                return
            if method == "GET" and path == "/api/context":
                self._json_result(api.context_document(store, actor))
                return
            if method == "GET" and path == "/api/tools":
                self._json_result(api.tool_catalog())
                return
            if method == "GET" and path == "/api/surface":
                self._json_result(api.surface_document(store, actor))
                return
            if method == "GET" and path == "/api/health":
                self._json_result(api.health_document(store, actor))
                return
            if method == "GET" and path.startswith("/api/why/"):
                slug = unquote(path[len("/api/why/") :])
                self._json_result(api.reason_document(store, slug, actor))
                return
            if method == "GET" and path.startswith("/api/doc/"):
                slug = unquote(path[len("/api/doc/") :])
                self._json_result(api.show_document(store, slug, actor))
                return
            if method == "POST" and path.startswith("/api/tool/"):
                self._call(path[len("/api/tool/") :])
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown route")

        def _call(self, name: str) -> None:
            length = self.headers.get("Content-Length")
            try:
                size = int(length) if length is not None else 0
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "VALIDATION_FAILED", "bad length")
                return
            if size < 0:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "VALIDATION_FAILED",
                    "the request length must not be negative",
                )
                return
            if size > MAX_BODY:
                self._error(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "VALIDATION_FAILED",
                    "the request body is too large",
                )
                return
            raw = self.rfile.read(size) if size else b""
            try:
                arguments = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "VALIDATION_FAILED",
                    "the body must be a JSON object",
                )
                return
            if not isinstance(arguments, dict):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "VALIDATION_FAILED",
                    "the body must be a JSON object",
                )
                return
            try:
                result = api.call_tool(store, unquote(name), arguments, actor)
            except Exception as error:  # the gateway's own refusal shapes
                code = getattr(error, "code", "INTERNAL")
                status = (
                    HTTPStatus.NOT_FOUND
                    if code == "NOT_FOUND"
                    else HTTPStatus.FORBIDDEN
                    if code == "FORBIDDEN"
                    else HTTPStatus.BAD_REQUEST
                )
                self._error(status, code, str(error))
                return
            self._json_result(result)

        def _host_allowed(self) -> bool:
            """Accept the IPv4 loopback names this server actually binds.

            The socket is IPv4-only, so ``[::1]`` is deliberately not accepted:
            a Host or Origin that names an address the server never listens on
            is a cross-origin request, not this page's own fetch.
            """
            host = (self.headers.get("Host") or "").strip().lower()
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if host not in allowed:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parts = urlsplit(origin)
                return f"{parts.hostname}:{parts.port}" in allowed
            return True

        def _token_ok(self, query: str) -> bool:
            supplied = parse_qs(query).get("token", [""])[0]
            if not supplied:
                supplied = self.headers.get(_HEADER) or ""
            if not supplied:
                cookie = self.headers.get("Cookie") or ""
                for part in cookie.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == _COOKIE:
                        supplied = value
                        break
            if not supplied:
                return False
            return hmac.compare_digest(
                supplied.encode("utf-8"), token.encode("ascii")
            )

        def _json_result(self, result: dict[str, Any]) -> None:
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._json(status, result)

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: HTTPStatus, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: HTTPStatus, code: str, message: str) -> None:
            self._json(status, {"ok": False, "error": {"code": code, "message": message}})

        def _security_headers(self) -> None:
            self.send_header("Content-Security-Policy", _CSP)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

    class _LoopbackServer(ThreadingHTTPServer):
        daemon_threads = True
        # On Windows SO_REUSEADDR lets a second socket steal the port, so the UI
        # binds exclusively there; POSIX keeps the usual fast restart.
        allow_reuse_address = sys.platform != "win32"

    try:
        server = _LoopbackServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise WriteRefused(
            "CONFLICT",
            f"the UI cannot bind 127.0.0.1:{port}: {exc}",
            {"port": port},
        ) from None
    server.daemon_threads = True
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return SimpleNamespace(server=server, token=token, base=base, store=store)


def serve(store: Path, *, actor: str = "human", port: int = 0) -> None:
    """Run the UI until interrupted; the URL (with token) goes to stderr only."""
    created = create_server(store, actor=actor, port=port)
    url = f"{created.base}/?token={created.token}"
    print(url, file=sys.stderr, flush=True)
    try:
        created.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        created.server.server_close()
