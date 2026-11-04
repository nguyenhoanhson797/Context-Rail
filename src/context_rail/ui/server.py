"""A loopback-only HTTP surface: the legacy React bundle plus the engine's JSON.

The page is the copied context-rail-link SPA built into ``ui/static``. It reads
one snapshot, one health document, named reads and the tool table, all backed by
``ui.api`` and therefore by the current engine.
"""

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

__all__ = ["MAX_BODY", "create_server", "serve"]

MAX_BODY = 2 * 1024 * 1024
REQUEST_TIMEOUT_S = 30.0
_COOKIE = "rail_ui_token"
_HEADERS = ("X-Rail-Token", "X-Context-Rail-Token")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'"
)
_STATIC = Path(__file__).with_name("static")
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".map": "application/json; charset=utf-8",
}


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
            try:
                self._dispatch("GET")
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # A reload or navigation closed the socket; there is no client to answer.
                return

        def do_POST(self) -> None:
            try:
                self._dispatch("POST")
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

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
            if method == "GET" and path in ("/", "/index.html"):
                self._page()
                return
            if method == "GET" and (path.startswith("/assets/") or path == "/favicon.svg"):
                self._asset(path)
                return
            if method != "GET" and method != "POST":
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown route")
                return
            if method == "GET" and path == "/api/context":
                self._safe(api.context_document, store, actor)
                return
            if method == "GET" and path == "/api/snapshot":
                self._safe(api.context_document, store, actor)
                return
            if method == "GET" and path == "/api/health":
                self._safe(api.health_document, store, actor)
                return
            if method == "GET" and path == "/api/tools":
                self._safe(api.tools_document)
                return
            if method == "GET" and path == "/api/projects":
                self._safe(api.projects_document, store)
                return
            if method == "GET" and path == "/api/portfolio":
                self._safe(api.portfolio_document, store, actor)
                return
            if method == "GET" and path.startswith("/api/doc/"):
                self._safe(api.doc_document, store, unquote(path[len("/api/doc/") :]), actor)
                return
            if method == "GET" and path.startswith("/api/read/"):
                self._safe(
                    api.read_document,
                    store,
                    unquote(path[len("/api/read/") :]),
                    actor,
                )
                return
            if method == "POST" and path.startswith("/api/tool/"):
                self._call(unquote(path[len("/api/tool/") :]))
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown route")

        def _safe(self, function: Any, *args: Any) -> None:
            try:
                self._json(HTTPStatus.OK, function(*args))
            except Exception as error:  # a read refusal is an envelope, not a crash
                self._refusal(error)

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
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "VALIDATION_FAILED",
                    "the body must be a JSON object",
                )
                return
            if not isinstance(body, dict):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "VALIDATION_FAILED",
                    "the body must be a JSON object",
                )
                return
            arguments = body.get("args") if isinstance(body.get("args"), dict) else {}
            try:
                result = api.call_tool(store, name, arguments, actor)
            except WriteRefused as error:
                status = (
                    HTTPStatus.FORBIDDEN
                    if error.code == "FORBIDDEN"
                    else HTTPStatus.OK
                )
                self._json(
                    status,
                    {"status": "error", "code": error.code, "error": error.message},
                )
                return
            except Exception as error:
                self._json(
                    HTTPStatus.OK,
                    {"status": "error", "error": str(error) or error.__class__.__name__},
                )
                return
            self._json(HTTPStatus.OK, result)

        def _refusal(self, error: Exception) -> None:
            code = getattr(error, "code", "INTERNAL")
            self._json(
                HTTPStatus.OK,
                {"status": "error", "code": code, "error": str(error)},
            )

        def _host_allowed(self) -> bool:
            """Accept the IPv4 loopback names this server actually binds."""
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
                for header in _HEADERS:
                    supplied = self.headers.get(header) or ""
                    if supplied:
                        break
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

        def _page(self) -> None:
            index = _STATIC / "index.html"
            if not index.is_file():
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "INTERNAL",
                    "the UI bundle is not built; run npm --prefix web run build",
                )
                return
            html = index.read_text(encoding="utf-8").replace(
                "__CTX_RAIL_TOKEN__", token
            )
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _MIME[".html"])
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Set-Cookie",
                f"{_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict",
            )
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _asset(self, path: str) -> None:
            relative = "favicon.svg" if path == "/favicon.svg" else path[len("/") :]
            target = (_STATIC / relative).resolve()
            root = _STATIC.resolve()
            if root not in target.parents or not target.is_file():
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown asset")
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", _MIME.get(target.suffix, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: HTTPStatus, code: str, message: str) -> None:
            self._json(status, {"status": "error", "code": code, "error": message})

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
