"""Bounded command execution: scrubbed environment, redaction and a hard timeout.

A command is a list of arguments and runs without a shell. The child sees only
the allow-listed parent variables plus ``extra_env``, secret-bearing values are
scrubbed from what is returned, and output is reduced to a head and a tail
inside ``max_output`` bytes without losing the digest of the whole stream.

Output streams to a private temporary directory (so a chatty child cannot fill
a pipe) and is reduced in fixed-size chunks, so memory stays bounded while the
digest still covers every byte. The temporary bytes are deleted before the
result is returned.
"""

from __future__ import annotations

import codecs
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from context_rail.store.location import PathEscape

DEFAULT_ENV_ALLOW = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
)

SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)

REDACTED = "[redacted]"
_MARKER = "\n[omitted]\n"
_MIN_SECRET = 6
_READ_CHUNK = 65536


def redact(text: str, secrets: list[str]) -> str:
    """Replace every secret value of length six or more with ``[redacted]``."""
    values = _secret_tuple(secrets)
    if not values:
        return text
    pattern = re.compile("|".join(re.escape(value) for value in values))
    return pattern.sub(REDACTED, text)


def run(
    command: list[str],
    *,
    cwd: Path,
    confine_to: Path,
    timeout: float,
    env_allow: tuple[str, ...] = DEFAULT_ENV_ALLOW,
    extra_env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    max_output: int = 24000,
) -> dict:
    """Run ``command`` inside ``confine_to`` and return bounded output.

    ``cwd`` outside ``confine_to`` raises :class:`PathEscape` before anything
    starts. An inherited allow-list entry whose name looks like a secret raises
    ``ValueError``, because it would hand a live secret to the child.
    """
    if isinstance(command, str) or not command:
        raise TypeError("command must be a non-empty list of arguments")
    if not all(isinstance(argument, str) for argument in command):
        raise TypeError("every argument must be text")
    root = Path(cwd).resolve()
    boundary = Path(confine_to).resolve()
    if not root.is_relative_to(boundary):
        raise PathEscape(f"{cwd} is outside {confine_to}")
    child_env = _child_env(env_allow, extra_env)
    secrets = _secret_values(extra_env)
    cap = max(0, max_output)
    head_cap, tail_cap, marker = _budgets(cap)
    digest = hashlib.sha256()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ctx-rail-run-") as temporary:
        directory = Path(temporary)
        stdout_path = directory / "stdout.txt"
        stderr_path = directory / "stderr.txt"
        stdin_file = None
        exit_code: int | None = None
        timed_out = False
        try:
            if stdin_text is not None:
                stdin_file = (directory / "stdin.txt").open("wb")
                stdin_file.write(stdin_text.encode("utf-8"))
                stdin_file.seek(0)
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                proc = subprocess.Popen(
                    list(command),
                    cwd=str(root),
                    env=child_env,
                    stdin=stdin_file if stdin_file is not None else subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    shell=False,
                    start_new_session=os.name != "nt",
                )
                try:
                    exit_code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _stop_tree(proc)
        finally:
            if stdin_file is not None:
                stdin_file.close()
        duration_s = round(time.monotonic() - started, 3)
        stdout = _Capture(secrets, head_cap, tail_cap, digest)
        stderr = _Capture(secrets, head_cap, tail_cap, digest)
        _reduce(stdout_path, stdout, secrets)
        _reduce(stderr_path, stderr, secrets)
        bounded = _assemble(cap, head_cap, tail_cap, marker, stdout, stderr)
    return {
        "exit": exit_code,
        "timed_out": timed_out,
        "duration_s": duration_s,
        "stdout": bounded["stdout"],
        "stderr": bounded["stderr"],
        "omitted_bytes": bounded["omitted_bytes"],
        "digest": "sha256:" + digest.hexdigest(),
    }


def _child_env(
    env_allow: tuple[str, ...], extra_env: dict[str, str] | None
) -> dict[str, str]:
    for name in env_allow:
        if SECRET_NAME.search(name) and name in os.environ:
            raise ValueError(
                f"refusing to inherit secret-named variable {name!r} into the child"
            )
    env = {name: os.environ[name] for name in env_allow if name in os.environ}
    if extra_env:
        env.update(extra_env)
    return env


def _secret_values(extra_env: dict[str, str] | None) -> tuple[str, ...]:
    values = {value for name, value in os.environ.items() if SECRET_NAME.search(name)}
    if extra_env:
        values |= {
            value for name, value in extra_env.items() if SECRET_NAME.search(name)
        }
    return _secret_tuple(values)


def _secret_tuple(values) -> tuple[str, ...]:
    kept = {
        value
        for value in values
        if isinstance(value, str) and len(value) >= _MIN_SECRET
    }
    return tuple(sorted(kept, key=len, reverse=True))


def _budgets(max_output: int) -> tuple[int, int, str]:
    """Head and tail byte budgets plus the omission marker that must fit."""
    cap = max(0, max_output)
    marker = _MARKER if cap >= 2 * len(_MARKER.encode("utf-8")) else ""
    head_cap = cap // 2
    tail_cap = cap - head_cap - len(marker.encode("utf-8"))
    return head_cap, tail_cap, marker


def _stop_tree(proc: subprocess.Popen) -> None:
    """Kill a timed-out child and its descendants, then reap the child."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait()


class _Capture:
    """The head, tail, byte count and shared digest of one output stream."""

    def __init__(self, secrets: tuple[str, ...], head_cap: int, tail_cap: int, digest):
        self._secrets = secrets
        self._pending = ""
        self._head_cap = head_cap
        self._tail_cap = tail_cap
        self._digest = digest
        self.head = b""
        self.tail = b""
        self.total = 0

    def feed(self, text: str, *, final: bool = False) -> None:
        if not text and not final:
            return
        if self._secrets:
            pending = self._pending + text
            emitted, self._pending = _redact_prefix(pending, self._secrets, final)
        else:
            emitted, self._pending = text, ""
        if not emitted:
            return
        data = emitted.encode("utf-8")
        self._digest.update(data)
        self.total += len(data)
        if len(self.head) < self._head_cap:
            room = self._head_cap - len(self.head)
            self.head += data[:room]
            data = data[room:]
        if data and self._tail_cap:
            kept = (self.tail + data)[-self._tail_cap :]
            self.tail = kept


def _redact_prefix(
    text: str, secrets: tuple[str, ...], final: bool
) -> tuple[str, str]:
    """Redact ``text`` up to a safe boundary and return what to hold back.

    A secret of length L can straddle the boundary, so only the first
    ``len(text) - (L - 1)`` characters are safe to emit; everything after it is
    returned unemitted and prefixed to the next chunk.
    """
    if final:
        limit = len(text)
    else:
        limit = max(0, len(text) - (len(secrets[0]) - 1))
    if limit == 0:
        return "", text
    pieces: list[str] = []
    index = 0
    while index < limit:
        hit_at = -1
        hit = ""
        for secret in secrets:
            found = text.find(secret, index)
            if found != -1 and found < limit and (hit_at == -1 or found < hit_at):
                hit_at, hit = found, secret
        if hit_at == -1:
            pieces.append(text[index:limit])
            index = limit
            break
        pieces.append(text[index:hit_at])
        pieces.append(REDACTED)
        index = hit_at + len(hit)
    return "".join(pieces), text[max(limit, index) :]


def _reduce(path: Path, capture: _Capture, secrets: tuple[str, ...]) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK):
            capture.feed(decoder.decode(chunk, final=False))
        capture.feed(decoder.decode(b"", final=True), final=True)


def _assemble(
    cap: int,
    head_cap: int,
    tail_cap: int,
    marker: str,
    out: _Capture,
    err: _Capture,
) -> dict:
    """Keep the combined head and tail of stdout followed by stderr."""
    out_head = out.head
    err_head = err.head[: max(0, head_cap - len(out_head))]
    head_owner = "stderr" if err_head else "stdout"

    err_room = max(0, err.total - len(err_head))
    err_take = min(err_room, tail_cap)
    err_tail = err.tail[-err_take:] if err_take else b""
    out_room = max(0, out.total - len(out_head))
    out_take = min(out_room, max(0, tail_cap - err_take))
    out_tail = out.tail[-out_take:] if out_take else b""
    omitted = out.total + err.total - len(out_head) - len(err_head)
    omitted -= len(out_tail) + len(err_tail)

    if omitted:
        if head_owner == "stderr":
            err_head = _trim_head_end(err_head)
        else:
            out_head = _trim_head_end(out_head)
        if out_tail:
            out_tail = _trim_tail_start(out_tail)
        elif err_tail:
            err_tail = _trim_tail_start(err_tail)
        omitted = out.total + err.total - len(out_head) - len(err_head)
        omitted -= len(out_tail) + len(err_tail)
    marker_bytes = marker.encode("utf-8") if omitted else b""

    stdout = out_head + (marker_bytes if head_owner == "stdout" else b"") + out_tail
    stderr = err_head + (marker_bytes if head_owner == "stderr" else b"") + err_tail
    if len(stdout) + len(stderr) > cap:
        stdout, stderr = _fit(stdout, stderr, cap)
    return {
        "stdout": bytes(stdout).decode("utf-8", errors="ignore"),
        "stderr": bytes(stderr).decode("utf-8", errors="ignore"),
        "omitted_bytes": max(0, omitted),
    }


def _fit(out, err, cap: int) -> tuple[bytes, bytes]:
    """Shrink the pair to ``cap`` bytes when a marker landed on a short tail."""
    if cap <= 0:
        return b"", b""
    overflow = len(out) + len(err) - cap
    if overflow <= 0:
        return bytes(out), bytes(err)
    out, err = bytearray(out), bytearray(err)
    while overflow > 0:
        target = out if len(out) >= len(err) else err
        drop = min(overflow, len(target))
        del target[len(target) - drop :]
        overflow -= drop
    return bytes(out), bytes(err)


def _trim_head_end(data: bytes) -> bytes:
    end = len(data)
    while end and (data[end - 1] & 0xC0) == 0x80:
        end -= 1
    return data[:end]


def _trim_tail_start(data: bytes) -> bytes:
    start = 0
    while start < len(data) and (data[start] & 0xC0) == 0x80:
        start += 1
    return data[start:]
