"""The twenty commands an agent runs in a session, one process each.

Informational, not a gate. ``py tests/perf/session_bench.py`` prints the same
session against a daemon and against direct calls, next to bare Python.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_rail.gateway.daemon import daemon_alive, stop_daemon
from helpers import child_env
from perf.gen import make_store

__all__ = ["COMMANDS", "bare_python_ms", "main", "run", "start_daemon"]

#: ``{etag:SLUG}`` is the etag the session's earlier ``read show SLUG`` returned.
COMMANDS: tuple[tuple[str, ...], ...] = (
    ("overview",),
    ("next",),
    ("types",),
    ("read", "status"),
    ("read", "find", "--preset", "task", "--limit", "20"),
    ("read", "show", "t1"),
    ("read", "show", "t2"),
    ("read", "show", "t3"),
    ("explain", "task"),
    ("why", "t1"),
    ("model",),
    ("next",),
    ("change", "add", "task", "Bench A"),
    ("change", "add", "task", "Bench B"),
    ("change", "update", "t1", "--expect", "{etag:t1}", "--tag", "+bench"),
    ("change", "update", "t2", "--expect", "{etag:t2}", "--tag", "+bench"),
    ("change", "update", "t3", "--expect", "{etag:t3}", "--tag", "+bench"),
    ("read", "find", "--text", "bench"),
    ("read", "show", "t1"),
    ("overview",),
)

_ETAG = re.compile(r"\{etag:([^}]+)\}")


def _cli(store: Path, args: list[str], env: dict[str, str]) -> tuple[float, bytes]:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "context_rail", "--store", str(store), *args],
        capture_output=True,
        env=env,
        input=b"",
        timeout=300,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    if proc.returncode != 0:
        raise RuntimeError(
            f"rail {' '.join(args)} exited {proc.returncode}: "
            f"{(proc.stdout + proc.stderr).decode('utf-8', 'replace')[:400]}"
        )
    return elapsed, proc.stdout


def start_daemon(store: Path, env: dict[str, str]) -> None:
    """The first call starts the daemon; wait for it, then warm its imports."""
    _cli(store, ["next"], env)
    deadline = time.monotonic() + 15
    while not daemon_alive(store):
        if time.monotonic() > deadline:
            raise RuntimeError("the daemon did not start within 15 s")
        time.sleep(0.05)
    _cli(store, ["next"], env)


def bare_python_ms(runs: int = 7) -> float:
    """Median wall time of ``python -c pass``, the floor any command pays."""
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True, input=b"")
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def run(store: Path, *, daemon: bool) -> dict[str, float | int]:
    """Run ``COMMANDS`` against ``store`` and time each process.

    With ``daemon`` the daemon is started and warmed first and every command is
    served by it; without, every command runs direct.
    """
    env = child_env({"RAIL_NO_DAEMON": None if daemon else "1"})
    etags: dict[str, str] = {}
    reads: list[float] = []
    writes: list[float] = []
    output = 0
    if daemon:
        start_daemon(store, env)
    try:
        for template in COMMANDS:
            args = [_ETAG.sub(lambda found: etags[found.group(1)], part) for part in template]
            elapsed, stdout = _cli(store, args, env)
            output += len(stdout)
            (writes if template[0] == "change" else reads).append(elapsed)
            if template[:2] == ("read", "show"):
                etags[template[2]] = json.loads(stdout)["value"]["doc"]["etag"]
    finally:
        if daemon:
            stop_daemon(store)
    return {
        "total_ms": sum(reads) + sum(writes),
        "median_ms": statistics.median(reads + writes),
        "read_median_ms": statistics.median(reads),
        "write_median_ms": statistics.median(writes),
        "output_bytes": output,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        results: dict[str, dict[str, float | int]] = {}
        for mode in ("direct", "daemon"):
            folder = Path(root) / mode
            folder.mkdir()
            results[mode] = run(make_store(folder, 200), daemon=mode == "daemon")
    floor = bare_python_ms()
    print(f"python -c pass: {floor:.0f} ms (median of 7)")
    print(f"{len(COMMANDS)} commands, one process each, store of 200 documents")
    for mode, found in results.items():
        print(
            f"{mode:>7}: total {found['total_ms']:.0f} ms, median {found['median_ms']:.0f} ms, "
            f"output {found['output_bytes']} bytes"
        )
        for kind in ("read", "write"):
            median = found[f"{kind}_median_ms"]
            print(f"         {kind} median {median:.0f} ms = bare Python + {median - floor:.0f} ms")


if __name__ == "__main__":
    main()
