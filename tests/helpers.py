"""Shared plain helpers for the Context Rail test suite."""

from __future__ import annotations

import json
import multiprocessing
import os
import queue as queue_module
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from context_rail.core.util import dump_frontmatter, load_yaml
from context_rail.store.doc import record_digest

_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
_STARTUP_TIMEOUT = 120.0
_JOIN_TIMEOUT = 30.0
DEFAULT_FAKE_BEHAVIORS = ("success",)


def run_cli(
    cwd: Path, *args: str, store: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``py -m context_rail`` in ``cwd`` against the repo's ``src`` tree.

    The global ``--store`` goes before the subcommand when ``store`` is given.
    ``RAIL_ACTOR`` is dropped from the child environment so a developer's shell
    never changes which actor a test's records name.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)
    env.pop("RAIL_ACTOR", None)
    command = [sys.executable, "-m", "context_rail"]
    if store is not None:
        command += ["--store", str(store)]
    command += [str(arg) for arg in args]
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def start_cli(
    cwd: Path, *args: str, store: Path | None = None
) -> subprocess.Popen[str]:
    """Start ``py -m context_rail`` without waiting, for crash scenarios.

    Uses the same environment as :func:`run_cli`; the caller owns the process
    and must terminate it. Output is captured as text.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)
    env.pop("RAIL_ACTOR", None)
    command = [sys.executable, "-m", "context_rail"]
    if store is not None:
        command += ["--store", str(store)]
    command += [str(arg) for arg in args]
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        start_new_session=os.name != "nt",
    )


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``cwd``; an error fails the calling test setup."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def process_alive(pid: int) -> bool:
    """True while the OS still lists ``pid`` as a running process."""
    if os.name == "nt":
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
    return True


def write_fake_droid(root: Path, behaviors: Sequence[Any]) -> Path:
    """Install the scripted fake droid under ``root`` and return its script."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "fakes" / "droid.py"
    script = root / "fake-droid.py"
    script.write_bytes(source.read_bytes())
    (root / "fake-droid.json").write_text(
        json.dumps({"behaviors": list(behaviors)}), encoding="utf-8", newline="\n"
    )
    (root / "fake-droid-cursor.json").write_text(
        '{"launches": 0}', encoding="utf-8", newline="\n"
    )
    return script


class FakeDroid:
    """A scripted fake ``droid`` installed under one temporary root."""

    def __init__(self, root: Path, behaviors: Sequence[Any]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.script = write_fake_droid(self.root, behaviors)

    def set_behaviors(self, behaviors: Sequence[Any]) -> "FakeDroid":
        """Replace the scripted behaviors and rewind the behavior cursor."""
        write_fake_droid(self.root, behaviors)
        return self

    def log(self) -> list[dict[str, Any]]:
        """One record per launch, in order: argv, env, prompt and session id."""
        path = self.root / "fake-droid-argv.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def session_ids(self) -> list[str]:
        """The session id each launch reported, in order."""
        return [str(row.get("session_id")) for row in self.log()]


def write_record(
    store: Path,
    record_id: str,
    kind: str,
    data: dict[str, Any],
    *,
    at: str = "2026-10-28T00:00:00Z",
    actor: str = "cto",
    about: tuple[str, ...] = (),
    preset: str = "event",
) -> Path:
    """Write one JSON Record with a valid digest and return its path."""
    payload = {
        "id": record_id,
        "primitive": "record",
        "preset": preset,
        "kind": kind,
        "at": at,
        "actor": actor,
        "about": list(about),
        "data": data,
    }
    payload["digest"] = record_digest(payload)
    path = Path(store) / "records" / f"{at[:4]}-{at[5:7]}" / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return path


def latest_record(store: Path, kind: str) -> dict[str, Any]:
    """The newest JSON Record of ``kind`` by ``(at, id)``.

    Raises ``LookupError`` when the store holds no such Record.
    """
    found: list[dict[str, Any]] = []
    for path in (Path(store) / "records").rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == kind:
            found.append(payload)
    if not found:
        raise LookupError(f"no {kind} record")
    return max(found, key=lambda row: (str(row.get("at") or ""), str(row.get("id") or "")))


def touch_doc(store: Path, rel: str, **frontmatter: Any) -> None:
    """Replace frontmatter keys in an existing document and bump its mtime.

    The body is preserved and the mtime moves two seconds ahead, so a change is
    visible even on filesystems with coarse timestamps.
    """
    path = Path(store) / rel
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{rel} has no frontmatter")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    payload = dict(load_yaml("\n".join(lines[1:end])) or {})
    payload.update(frontmatter)
    body = "\n".join(lines[end + 1 :])
    path.write_text(
        "---\n" + dump_frontmatter(payload) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def approve_picture(
    store: Path, *, confirmer: str = "validator", expect: str | None = None
) -> dict[str, Any]:
    """Approve the current picture as the human, sending the displayed digest.

    ``expect`` defaults to the ``snapshot_digest`` ``show charter`` displays, so
    a re-approval after a mismatch only needs the new digest passed in.
    """
    from context_rail.gateway.gateway import invoke

    shown = invoke("show", {"slug": "charter"}, store=store, actor="human")
    if expect is None:
        expect = shown["value"]["approval"]["snapshot_digest"]
    return invoke(
        "approve",
        {
            "target": "charter",
            "confirm": "APPROVE charter",
            "confirmer": confirmer,
            "expect": expect,
        },
        store=store,
        actor="human",
    )


def median_ms(fn: Callable[[], object], runs: int) -> float:
    """Median wall time of ``runs`` calls to ``fn``, in milliseconds."""
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def _move_worker(
    store: str,
    slug: str,
    etag: str,
    actor: str,
    barrier: Any,
    results: Any,
) -> None:
    """One spawned mover: announce readiness, wait for the release, then move."""
    from context_rail.gateway.gateway import invoke

    results.put(("ready", actor))
    try:
        barrier.wait(timeout=_STARTUP_TIMEOUT)
        result = invoke(
            "move",
            {"slug": slug, "status": "doing", "expect": etag},
            store=Path(store),
            actor=actor,
        )
    except BaseException as exc:  # surface a child fault in the parent
        results.put(("failed", actor, f"{type(exc).__name__}: {exc}"))
        raise
    results.put(("result", actor, result))


def parallel_moves(store: Path, slug: str, etag: str, n: int) -> list[dict[str, Any]]:
    """Run ``n`` simultaneous ``move`` calls that all expect the same etag.

    The workers spawn, announce readiness, and are released together by a
    barrier; every child is joined, a straggler is terminated, and a child
    exception or a missing result fails the call instead of being hidden.
    """
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    barrier = context.Barrier(n + 1)
    workers = [
        context.Process(
            target=_move_worker,
            args=(str(store), slug, etag, f"w{index}", barrier, results),
            name=f"mover-{index}",
        )
        for index in range(n)
    ]
    for worker in workers:
        worker.start()
    try:
        _await_readiness(results, n)
        barrier.wait(timeout=_STARTUP_TIMEOUT)
        collected = _collect_results(results, n)
    finally:
        for worker in workers:
            worker.join(timeout=_JOIN_TIMEOUT)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=_JOIN_TIMEOUT)
    return collected


def _await_readiness(results: Any, n: int) -> None:
    """Wait until every worker announced it is ready to be released."""
    seen = 0
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while seen < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"only {seen} of {n} movers became ready in time")
        try:
            message = results.get(timeout=remaining)
        except queue_module.Empty:
            raise AssertionError(f"only {seen} of {n} movers became ready in time") from None
        if message[0] != "ready":
            raise AssertionError(f"a mover failed before the release: {message!r}")
        seen += 1


def _collect_results(results: Any, n: int) -> list[dict[str, Any]]:
    """Collect every worker's Result, failing on a child fault or a shortfall."""
    collected: list[dict[str, Any]] = []
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while len(collected) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"only {len(collected)} of {n} movers reported a result")
        try:
            message = results.get(timeout=remaining)
        except queue_module.Empty:
            raise AssertionError(
                f"only {len(collected)} of {n} movers reported a result"
            ) from None
        if message[0] == "failed":
            raise AssertionError(f"mover {message[1]} failed: {message[2]}")
        if message[0] != "result":
            raise AssertionError(f"unexpected mover message: {message!r}")
        collected.append(message[2])
    return collected
