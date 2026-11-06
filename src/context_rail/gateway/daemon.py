"""A loopback daemon that keeps one warm Python process for one store.

The daemon caches the process, not the store: every call builds a fresh
Context, so a checkout or a hand edit is seen at once. It is not a second
write path: every call still goes through ``gateway.invoke()`` ->
``store/write.py:commit()``.
The transport is TCP on 127.0.0.1, gated by a per-store token recorded in
the descriptor, which never leaves the worktree's git dir. The CLI reads
the descriptor and falls back to direct invoke on any miss, so a stale
daemon can never block a command.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import threading
from pathlib import Path
from typing import Any

from context_rail.core.util import sha256_hex
from context_rail.store.location import local_dir

__all__ = [
    "DAEMON_TIMEOUT_MS",
    "NO_DAEMON_ENV",
    "daemon_endpoint",
    "daemon_alive",
    "try_daemon",
    "serve",
    "clear_daemon_descriptor",
    "store_hash",
]

DAEMON_TIMEOUT_MS = 2000
DAEMON_ENV_TIMEOUT = "RAIL_DAEMON_TIMEOUT_MS"
NO_DAEMON_ENV = "RAIL_NO_DAEMON"
_DAEMON_FILE = "daemon.json"
_PID_FILE = "daemon.pid"
_MAX_FRAME = 16 * 1024 * 1024


def daemon_endpoint(store: Path) -> Path | None:
    """The daemon descriptor path for one store, under ``local_dir``."""
    return local_dir(Path(store)) / _DAEMON_FILE


def daemon_alive(store: Path) -> bool:
    """Whether a daemon descriptor exists and names a running process."""
    descriptor = daemon_endpoint(store)
    if descriptor is None or not descriptor.is_file():
        return False
    payload = _read_descriptor(descriptor)
    if payload is None:
        return False
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import subprocess

        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return str(pid) in listed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def try_daemon(
    store: Path | None,
    tool: str,
    params: dict[str, Any],
    actor: str | None,
    actor_env: str | None = None,
) -> dict[str, Any] | None:
    """Serve one call from the daemon; ``None`` falls back to direct invoke."""
    if os.environ.get(NO_DAEMON_ENV):
        return None
    if store is None:
        return None
    canonical = tool
    if canonical in ("mcp serve", "ui serve", "serve daemon", "sync driver"):
        return None
    descriptor = daemon_endpoint(Path(store))
    if descriptor is None or not descriptor.is_file():
        return None
    payload = _read_descriptor(descriptor)
    if payload is None:
        return None
    try:
        port = int(payload["port"])
        token = str(payload["token"])
    except (KeyError, TypeError, ValueError):
        return None
    if port <= 0 or not token:
        return None
    deadline_ms = _timeout_ms()
    try:
        import time

        deadline = time.monotonic() + deadline_ms / 1000.0
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(max(deadline_ms / 1000.0, 0.2))
        try:
            client.connect(("127.0.0.1", port))
            request = json.dumps(
                {
                    "tool": canonical,
                    "params": dict(params or {}),
                    "actor": actor,
                    "actor_env": actor_env,
                    "token": token,
                }
            ).encode("utf-8")
            client.sendall(request + b"\n")
            client.shutdown(socket.SHUT_WR)
            chunks = b""
            while True:
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    return None
                if time.monotonic() > deadline:
                    return None
                if not chunk:
                    break
                chunks += chunk
                if b"\n" in chunks:
                    break
        finally:
            client.close()
        payload_out = json.loads(chunks.decode("utf-8").strip())
        if not isinstance(payload_out, dict):
            return None
        if not isinstance(payload_out.get("ok"), bool):
            return None
        return payload_out
    except Exception:
        return None


def serve(store: Path, *, port: int = 0) -> int:
    """Serve one store over loopback TCP; blocks until interrupted."""
    server = _DaemonServer(Path(store), port=port)
    return server.start_blocking()


def write_daemon_descriptor(store: Path, *, port: int, pid: int | None = None) -> Path:
    """Persist daemon identity and token so later CLIs can find it."""
    store_path = Path(store)
    descriptor = daemon_endpoint(store_path)
    assert descriptor is not None
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    payload = json.dumps(
        {
            "pid": int(pid or os.getpid()),
            "port": int(port),
            "store": str(store_path),
            "token": token,
        },
        sort_keys=True,
    )
    descriptor.write_text(payload, encoding="utf-8", newline="\n")
    (descriptor.parent / _PID_FILE).write_text(str(pid or os.getpid()), encoding="utf-8")
    return descriptor


def clear_daemon_descriptor(store: Path) -> None:
    """Remove a stale daemon descriptor (doctor --fix safe path)."""
    descriptor = daemon_endpoint(store)
    if descriptor is not None and descriptor.is_file():
        try:
            descriptor.unlink()
        except OSError:
            pass
    try:
        stale = daemon_endpoint(store)
        if stale is not None:
            (stale.parent / _PID_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def store_hash(store: Path) -> str:
    """Stable hash of a store path (named-pipe fallback identity)."""
    return sha256_hex(str(Path(store).resolve()).encode("utf-8"))[:12]


def _timeout_ms() -> int:
    raw = os.environ.get(DAEMON_ENV_TIMEOUT)
    if raw:
        try:
            return max(int(raw), 50)
        except ValueError:
            return DAEMON_TIMEOUT_MS
    return DAEMON_TIMEOUT_MS


def _read_descriptor(descriptor: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class _DaemonServer:
    """TCP loopback server that runs each call in a warm process on a fresh Context."""

    def __init__(self, store: Path, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.store = Path(store)
        self.host = host
        self.port = port
        self._socket: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = ""

    def start_blocking(self) -> int:
        self.start()
        try:
            while not self._stop.is_set():
                threading.Event().wait(0.5)
        except KeyboardInterrupt:
            self.stop()
        return 0

    def start(self) -> int:
        """Bind, publish the descriptor and spawn the serving thread."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(16)
        self.port = int(sock.getsockname()[1])
        self._socket = sock
        descriptor = write_daemon_descriptor(self.store, port=self.port)
        self._token = json.loads(descriptor.read_text(encoding="utf-8"))["token"]
        self._thread = threading.Thread(target=self._serve, args=(sock,), daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout=2.0)
        clear_daemon_descriptor(self.store)

    def _serve(self, sock: socket.socket) -> None:
        sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(conn)
            except Exception:
                try:
                    conn.sendall(
                        b'{"ok": false, "error": {"code": "INTERNAL", "message": "the daemon could not run the call"}}\n'
                    )
                except OSError:
                    pass
            try:
                conn.close()
            except OSError:
                pass
        sock.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(_timeout_ms() / 1000.0 + 1.0)
        data = conn.recv(65536)
        if not data:
            return
        try:
            payload = json.loads(data.decode("utf-8").strip())
        except ValueError:
            conn.sendall(b'{"ok": false}\n')
            return
        if not isinstance(payload, dict):
            conn.sendall(b'{"ok": false}\n')
            return
        token = str(payload.get("token") or "")
        if not hmac.compare_digest(token, getattr(self, "_token", "")):
            conn.sendall(
                b'{"ok": false, "error": {"code": "PERMISSION_DENIED", "message": "the daemon token does not match"}}\n'
            )
            return
        from context_rail.gateway.execution import caller_identity
        from context_rail.gateway.gateway import invoke

        tool = payload.get("tool")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        actor_raw = payload.get("actor")
        actor = actor_raw if isinstance(actor_raw, str) else None
        env_raw = payload.get("actor_env")
        actor_env = env_raw if isinstance(env_raw, str) else None
        if not isinstance(tool, str) or not tool.strip():
            conn.sendall(b'{"ok": false}\n')
            return
        with caller_identity(actor, actor_env):
            try:
                result = invoke(tool, params, store=self.store, actor=actor)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL",
                        "message": str(exc) or exc.__class__.__name__,
                    },
                }
        conn.sendall((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
