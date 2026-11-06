"""A loopback daemon that keeps one warm Python process for one store.

The daemon caches the process, not the store: every call builds a fresh
Context, so a checkout or a hand edit is seen at once. It is not a second
write path: every call still goes through ``gateway.invoke()`` ->
``store/write.py:commit()``.
The transport is TCP on 127.0.0.1, gated by a per-store token recorded in
the descriptor, which never leaves the worktree's git dir. A client reads the
descriptor and falls back to a direct call on any miss, so a stale daemon can
never block a command. A write whose outcome is unknown is reported, never
run a second time.

Importing this module costs only the standard library, ``core.locate`` and
``core.result``: the CLI loads it before it may load YAML or SQLite.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

# hmac, secrets and threading serve only the daemon process. They cost about
# 25 ms together, which a client must not pay, so the server imports them where
# it uses them.

from context_rail import __version__
from context_rail.core.locate import PathEscape, local_dir
from context_rail.core.result import fail
from context_rail.core.slug import sha256_hex

__all__ = [
    "DAEMON_IDLE_S",
    "DAEMON_TIMEOUT_MS",
    "NO_DAEMON_ENV",
    "call_daemon",
    "clear_daemon_descriptor",
    "daemon_alive",
    "daemon_endpoint",
    "drop_unreachable",
    "process_alive",
    "serve",
    "spawn_daemon",
    "stop_daemon",
    "store_hash",
    "write_daemon_descriptor",
]

DAEMON_TIMEOUT_MS = 2000
DAEMON_IDLE_S = 600
DAEMON_ENV_TIMEOUT = "RAIL_DAEMON_TIMEOUT_MS"
DAEMON_ENV_IDLE = "RAIL_DAEMON_IDLE_S"
NO_DAEMON_ENV = "RAIL_NO_DAEMON"
#: Bumped when a request must mean something new. The descriptor is named for it,
#: so a client or daemon from another protocol never finds the other: an older
#: client would otherwise send no ``actor_env`` and run a worker as unnamed human.
_PROTOCOL = 2
_DAEMON_FILE = f"daemon-{_PROTOCOL}.json"
_SPAWN_FILE = "daemon.spawn"
_SPAWN_LOCK_STALE_S = 30
_STOP_WAIT_S = 5.0
_DETACHED_PROCESS = 0x8
_CREATE_NEW_PROCESS_GROUP = 0x200
_MAX_FRAME = 16 * 1024 * 1024
#: Windows takes about a second to refuse a connection to a closed loopback
#: port, while a live listener completes the connect at once (even when it is
#: busy: the kernel queues it), so a short cap loses nothing.
_CONNECT_CAP_S = 0.25
#: Long enough for that refusal to arrive, so a dead descriptor is told from a busy daemon.
_REACH_CHECK_S = 3.0
_MAX_PID = 2**31 - 1
_TOKEN_REFUSED = "daemon_token"
_EXPIRED = "daemon_expired"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def daemon_endpoint(store: Path) -> Path | None:
    """The daemon descriptor path for one store, under ``local_dir``."""
    return local_dir(Path(store)) / _DAEMON_FILE


def process_alive(pid: int) -> bool:
    """Whether ``pid`` is a running process, without starting one.

    ``os.kill(pid, 0)`` terminates the process on Windows, so Windows asks the
    kernel for the exit code instead.
    """
    if pid <= 0 or pid > _MAX_PID:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    # ``_winapi`` is built into the interpreter; ``ctypes`` costs about 13 ms to import.
    import _winapi

    try:
        handle = _winapi.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except OSError as error:
        return getattr(error, "winerror", None) == _ERROR_ACCESS_DENIED
    try:
        return bool(_winapi.GetExitCodeProcess(handle) == _STILL_ACTIVE)
    except OSError:
        return True
    finally:
        _winapi.CloseHandle(handle)


def daemon_alive(store: Path) -> bool:
    """Whether a daemon descriptor exists and names a running process."""
    held = _held_descriptor(store)
    return held is not None and process_alive(held[0])


def drop_unreachable(store: Path) -> bool:
    """Clear a descriptor whose process is alive but does not answer; True when cleared.

    A pid recorded before a reboot can belong to an unrelated process now, and
    the descriptor would then keep a new daemon from ever starting. A daemon
    that is merely busy still accepts the connection, so only a refusal counts.
    """
    held = _held_descriptor(store)
    if held is None:
        return False
    _, port, token, _ = held
    try:
        socket.create_connection(("127.0.0.1", port), timeout=_REACH_CHECK_S).close()
    except TimeoutError:
        return False
    except OSError:
        _release_descriptor(Path(store), token)
        return True
    return False


def _held_descriptor(store: Path) -> tuple[int, int, str, object] | None:
    """``(pid, port, token, version)`` from a well-formed descriptor, else None."""
    try:
        descriptor = daemon_endpoint(Path(store))
    except (PathEscape, OSError):
        return None
    payload = _read_descriptor(descriptor) if descriptor is not None else None
    if payload is None:
        return None
    pid, port, token = payload.get("pid"), payload.get("port"), payload.get("token")
    if not (_whole(pid, _MAX_PID) and _whole(port, 65535) and isinstance(token, str) and token):
        return None
    return pid, port, token, payload.get("version")


def _whole(value: object, high: int) -> bool:
    return type(value) is int and 0 < value <= high


def call_daemon(
    store: Path,
    tool: str,
    params: dict[str, Any],
    *,
    actor: str | None,
    actor_env: str | None,
    read_only: bool,
) -> dict[str, Any] | None:
    """Serve one call from the store's daemon; ``None`` means run it direct.

    A descriptor that is missing, stale, from another version or refused, and a
    connection that fails, are all misses with nothing sent. Once the request
    may have reached the daemon, a read can still be run direct, but a write may
    already have happened: it is reported as unknown and never repeated here.
    """
    if os.environ.get(NO_DAEMON_ENV):
        return None
    target = _live_target(Path(store))
    if target is None:
        return None
    port, token = target
    budget = _timeout_ms() / 1000.0
    deadline = time.monotonic() + budget
    try:
        request = json.dumps(
            {
                "tool": tool,
                "params": params,
                "actor": actor,
                "actor_env": actor_env,
                "token": token,
                # A busy daemon may reach this request after the caller has
                # stopped waiting; the daemon declines it rather than run a
                # write nobody is waiting for and the caller may repeat.
                "deadline": time.time() + budget,
            }
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    try:
        client = socket.create_connection(
            ("127.0.0.1", port), timeout=min(budget, _CONNECT_CAP_S)
        )
    except OSError:
        return None
    with client:
        try:
            client.settimeout(max(deadline - time.monotonic(), 0.05))
            client.sendall(request + b"\n")
            client.shutdown(socket.SHUT_WR)
        except OSError:
            return None if read_only else _unknown_outcome(params)
        envelope = _parse_reply(_read_reply(client, deadline))
    if envelope is None:
        return None if read_only else _unknown_outcome(params)
    if _declined(envelope):
        return None
    return envelope


def _live_target(store: Path) -> tuple[int, str] | None:
    """``(port, token)`` of a live, current daemon, else None and nothing sent."""
    held = _held_descriptor(store)
    if held is None:
        return None
    pid, port, token, version = held
    if not process_alive(pid):
        _release_descriptor(store, token)
        return None
    if version != __version__:
        _ask_to_stop(port, token)
        return None
    return port, token


def _ask_to_stop(port: int, token: str, timeout: float = 0.5) -> bool:
    """Ask a daemon to exit; True when it said it would."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(json.dumps({"op": "stop", "token": token}).encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            envelope = _parse_reply(_read_reply(sock, time.monotonic() + timeout))
    except OSError:
        return False
    return envelope is not None and envelope["ok"] is True


def spawn_daemon(store: Path) -> bool:
    """Start a detached daemon for ``store``; True when this call started one.

    It never waits for the daemon. Callers racing to start the first daemon
    take an exclusive lock file, which the daemon removes once its descriptor
    is written; a lock older than a crashed starter could leave is replaced.
    """
    store = Path(store)
    lock = _take_spawn_lock(store)
    if lock is None:
        return False
    if daemon_alive(store):
        _drop_spawn_lock(lock)
        return False
    import subprocess
    import tempfile

    options: dict[str, Any] = {
        "cwd": tempfile.gettempdir(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    # The daemon serves every caller and takes each one's identity from the
    # request, so it must not run as whichever actor happened to start it.
    options["env"] = {k: v for k, v in os.environ.items() if k != "RAIL_ACTOR"}
    if os.name == "nt":
        options["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    command = [sys.executable, "-m", "context_rail", "--store", str(store), "serve", "daemon"]
    try:
        subprocess.Popen(command, **options)
    except OSError:
        _drop_spawn_lock(lock)
        return False
    return True


def _take_spawn_lock(store: Path) -> Path | None:
    try:
        folder = local_dir(store)
    except (PathEscape, OSError):
        return None
    lock = folder / _SPAWN_FILE
    for _ in range(2):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue
            if age < _SPAWN_LOCK_STALE_S:
                return None
            _drop_spawn_lock(lock)
            continue
        except OSError:
            return None
        os.close(handle)
        return lock
    return None


def _drop_spawn_lock(lock: Path) -> None:
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass


def stop_daemon(store: Path) -> bool:
    """Stop the store's daemon; True when a live one was running and is now gone."""
    held = _held_descriptor(store)
    if held is None:
        return False
    pid, port, token, _ = held
    if not process_alive(pid):
        _release_descriptor(Path(store), token)
        return False
    if not _ask_to_stop(port, token, _timeout_ms() / 1000.0):
        return False
    deadline = time.monotonic() + _STOP_WAIT_S
    while process_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    _release_descriptor(Path(store), token)
    return True


def _read_reply(client: socket.socket, deadline: float) -> bytes | None:
    chunks: list[bytes] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        client.settimeout(remaining)
        try:
            chunk = client.recv(65536)
        except OSError:
            return None
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks) or None


def _parse_reply(raw: bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        envelope = json.loads(raw.decode("utf-8").strip())
    except ValueError:
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
        return None
    if envelope["ok"] is False and not isinstance(envelope.get("error"), dict):
        return None
    return envelope


def _declined(envelope: dict[str, Any]) -> bool:
    """Whether the daemon refused the request before running anything."""
    error = envelope.get("error")
    if not isinstance(error, dict):
        return False
    details = error.get("details")
    return isinstance(details, dict) and details.get("reason") in (_TOKEN_REFUSED, _EXPIRED)


def _unknown_outcome(params: dict[str, Any]) -> dict[str, Any]:
    if params.get("key"):
        message = "outcome unknown; repeating this exact command is safe, the key makes it idempotent"
    else:
        message = (
            "outcome unknown; do not repeat this command; "
            "re-read the item to see whether it applied"
        )
    return fail("PROCESS_ERROR", message, {"reason": "daemon_no_answer"})


def serve(store: Path, *, port: int = 0, idle_s: int | None = None) -> int:
    """Serve one store over loopback TCP; blocks until stopped or idle."""
    server = _DaemonServer(Path(store), port=port, idle_s=idle_s)
    return server.start_blocking()


def write_daemon_descriptor(store: Path, *, port: int, pid: int | None = None) -> Path:
    """Persist daemon identity and token so later CLIs can find it."""
    import secrets

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
            "version": __version__,
        },
        sort_keys=True,
    )
    # The token lets its holder act as any actor, so only the owner may read it.
    handle = os.open(descriptor, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as written:
        written.write(payload)
    if os.name != "nt":
        os.chmod(descriptor, 0o600)
    return descriptor


def clear_daemon_descriptor(store: Path) -> None:
    """Remove a stale daemon descriptor (doctor --fix safe path)."""
    descriptor = daemon_endpoint(store)
    if descriptor is not None:
        try:
            descriptor.unlink(missing_ok=True)
        except OSError:
            pass


def _release_descriptor(store: Path, token: str) -> None:
    """Remove the descriptor unless a newer daemon has already replaced it."""
    descriptor = daemon_endpoint(store)
    if descriptor is not None and descriptor.is_file():
        held = _read_descriptor(descriptor)
        if held is not None and held.get("token") != token:
            return
    clear_daemon_descriptor(store)


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


def _idle_seconds(explicit: int | None) -> int:
    """Seconds without a request before the daemon exits: the argument, the environment, else 600."""
    for candidate in (explicit, os.environ.get(DAEMON_ENV_IDLE)):
        try:
            seconds = int(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if seconds >= 1:
            return seconds
    return DAEMON_IDLE_S


def _read_descriptor(descriptor: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class _FrameTooLarge(Exception):
    pass


def _read_frame(conn: socket.socket) -> bytes:
    """One request: every byte up to the first newline, or to EOF."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        size += len(chunk)
        if size > _MAX_FRAME:
            raise _FrameTooLarge
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).split(b"\n", 1)[0]


class _DaemonServer:
    """TCP loopback server that runs each call in a warm process on a fresh Context."""

    def __init__(
        self,
        store: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        idle_s: int | None = None,
    ) -> None:
        import threading

        self.store = Path(store)
        self.host = host
        self.port = port
        self._idle_s = _idle_seconds(idle_s)
        self._last_active = time.monotonic()
        self._socket: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = ""

    def start_blocking(self) -> int:
        self.start()
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        self.stop()
        return 0

    def start(self) -> int:
        """Bind, publish the descriptor and spawn the serving thread."""
        import threading

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(16)
        self.port = int(sock.getsockname()[1])
        self._socket = sock
        descriptor = write_daemon_descriptor(self.store, port=self.port)
        self._token = json.loads(descriptor.read_text(encoding="utf-8"))["token"]
        _drop_spawn_lock(descriptor.parent / _SPAWN_FILE)
        self._last_active = time.monotonic()
        self._thread = threading.Thread(target=self._serve, args=(sock,), daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        import threading

        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        _release_descriptor(self.store, self._token)

    def _serve(self, sock: socket.socket) -> None:
        sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                if time.monotonic() - self._last_active >= self._idle_s:
                    self._stop.set()
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
            self._last_active = time.monotonic()
        sock.close()
        _release_descriptor(self.store, self._token)

    def _reply(self, conn: socket.socket, envelope: dict[str, Any]) -> None:
        conn.sendall((json.dumps(envelope) + "\n").encode("ascii"))

    def _handle(self, conn: socket.socket) -> None:
        import hmac

        conn.settimeout(_timeout_ms() / 1000.0 + 1.0)
        try:
            frame = _read_frame(conn)
        except _FrameTooLarge:
            self._reply(conn, fail("VALIDATION_FAILED", "the request is too large"))
            return
        except OSError:
            return
        if not frame:
            return
        try:
            payload = json.loads(frame.decode("utf-8"))
        except ValueError:
            conn.sendall(b'{"ok": false}\n')
            return
        if not isinstance(payload, dict):
            conn.sendall(b'{"ok": false}\n')
            return
        token = str(payload.get("token") or "")
        if not hmac.compare_digest(token.encode("utf-8"), self._token.encode("utf-8")):
            self._reply(
                conn,
                fail(
                    "PERMISSION_DENIED",
                    "the daemon token does not match",
                    {"reason": _TOKEN_REFUSED},
                ),
            )
            return
        if payload.get("op") == "stop":
            self._reply(conn, {"ok": True, "value": {"stopping": True}})
            self._stop.set()
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
        deadline = payload.get("deadline")
        if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
            if time.time() > deadline:
                self._reply(
                    conn,
                    fail(
                        "PROCESS_ERROR",
                        "the daemon was busy past the request's deadline and did not run it",
                        {"reason": _EXPIRED},
                    ),
                )
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
        try:
            self._reply(conn, result)
        except (TypeError, ValueError):
            # The call has run; a result that cannot be written is not a failure to retry.
            self._reply(conn, _unknown_outcome(params))
