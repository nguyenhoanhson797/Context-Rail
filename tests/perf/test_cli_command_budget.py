"""What a command costs as a process, with the daemon up, against bare Python."""

from __future__ import annotations

import itertools
import statistics
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from context_rail.gateway.daemon import daemon_alive, stop_daemon
from helpers import child_env, median_ms, run_cli_probe
from perf.gen import make_store
from perf.session_bench import start_daemon

_RUNS = 7
_READ_BUDGET_MS = 150
#: 1.5 x the 375 ms a served ``change add`` costs over bare Python, rounded up
#: to 25 ms. The write itself dominates: the commit.
_WRITE_BUDGET_MS = 575


@pytest.fixture
def served_store(
    tmp_path: Path, daemon_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    monkeypatch.delenv("RAIL_DAEMON_TIMEOUT_MS", raising=False)
    store = make_store(tmp_path, 20)
    start_daemon(store, child_env({"RAIL_NO_DAEMON": None}))
    try:
        yield store
    finally:
        stop_daemon(store)


def _bare() -> None:
    subprocess.run([sys.executable, "-c", "pass"], capture_output=True, input=b"", timeout=300)


def _rail(store: Path, *args: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "context_rail", "--store", str(store), "--actor", "cto", *args],
        capture_output=True,
        env=child_env({"RAIL_NO_DAEMON": None}),
        input=b"",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _overhead_ms(command: Callable[[], object]) -> float:
    """Median of ``_RUNS`` separate processes of ``command`` over bare Python.

    The two are sampled alternately, so a stretch of machine load slows both
    and cancels, instead of landing on whichever was measured second. One pair
    runs first and is discarded: the first process after a daemon starts reads
    files the later ones find cached.
    """
    _bare()
    command()
    bare: list[float] = []
    timed: list[float] = []
    for _ in range(_RUNS):
        bare.append(median_ms(_bare, 1))
        timed.append(median_ms(command, 1))
    return statistics.median(timed) - statistics.median(bare)


@pytest.mark.perf
def test_a_daemon_served_read_costs_under_150_ms_over_bare_python(served_store: Path) -> None:
    overhead = _overhead_ms(lambda: _rail(served_store, "next"))
    assert overhead < _READ_BUDGET_MS, f"rail next cost bare Python + {overhead:.0f} ms"
    assert daemon_alive(served_store)
    proc, loaded = run_cli_probe(
        served_store.parent, "--actor", "cto", "next", store=served_store
    )
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is False, "the call was not served by the daemon"


@pytest.mark.perf
def test_a_daemon_served_write_budget(served_store: Path) -> None:
    titles = itertools.count()
    overhead = _overhead_ms(
        lambda: _rail(served_store, "change", "add", "task", f"Budget write {next(titles)}")
    )
    assert overhead < _WRITE_BUDGET_MS, f"rail change add cost bare Python + {overhead:.0f} ms"
    assert daemon_alive(served_store)
    proc, loaded = run_cli_probe(
        served_store.parent,
        "--actor",
        "cto",
        "change",
        "add",
        "task",
        "Served write probe",
        store=served_store,
    )
    assert proc.returncode == 0, proc.stderr
    assert loaded["GW"] is False, "the write was not served by the daemon"


@pytest.mark.perf
def test_first_real_cli_call_and_result_write_measurement(tmp_path: Path) -> None:
    import time

    store = make_store(tmp_path, 200, with_results=True)
    body_file = tmp_path / "result.md"
    body_file.write_text(
        "## Summary\nFirst CLI.\n\n## Findings\nClean.\n\n## Decisions\nProceed.\n\n"
        "## Evidence\nMeasured.\n\n## Next steps\nReview.\n",
        encoding="utf-8",
        newline="\n",
    )
    cold_start = time.perf_counter()
    first = subprocess.run(
        [sys.executable, "-m", "context_rail", "--store", str(store), "--actor", "cto", "next"],
        capture_output=True,
        env=child_env({"RAIL_NO_DAEMON": "1"}),
        input=b"",
        timeout=300,
    )
    cold_ms = (time.perf_counter() - cold_start) * 1000.0
    assert first.returncode == 0, first.stdout + first.stderr

    write_start = time.perf_counter()
    recorded = subprocess.run(
        [
            sys.executable,
            "-m",
            "context_rail",
            "--store",
            str(store),
            "--actor",
            "cto",
            "loop",
            "result",
            "t0",
            "--body-file",
            str(body_file),
        ],
        capture_output=True,
        env=child_env({"RAIL_NO_DAEMON": "1"}),
        input=b"",
        timeout=300,
    )
    write_ms = (time.perf_counter() - write_start) * 1000.0
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    print(f"first cold CLI next @200+results: {cold_ms:.1f} ms; first CLI loop result: {write_ms:.1f} ms")
    assert cold_ms < 5000.0
    assert write_ms < 5000.0
